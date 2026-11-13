"""Hybrid retrieval: vector + full-text + exact error codes + trigram, merged with RRF (k=60).

Rules:
- The query is redacted first (same redactor as ingestion), so external calls and SQL only
  ever see redacted text. A fresh Redactor means IP placeholders may differ from stored ones.
- Full-text uses the 'simple' config (no stemming, no stop-word removal) and ORs the query
  tokens, ranked with ts_rank_cd. This is "full-text ranking", not BM25.
- RRF is rank based, so it cannot say "nothing relevant". The pass/fail decision uses
  evidence instead: a chunk passes if it matched an exact error code, shares the query's
  fingerprint, has a full-text hit, or has vector similarity >= min_similarity.
  If no chunk passes, the result is inconclusive and the LLM must be skipped.
- Embedding failure (or no provider): vector search is skipped, degraded=True.
- Fingerprint boost: chunks whose fingerprint equals the query's get +FINGERPRINT_BOOST.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.embedding_worker import ALLOWED_TABLES, vector_literal
from app.services.embeddings import EmbeddingProvider
from app.services.fingerprint import extract_error_codes, make_fingerprint
from app.services.redaction import Redactor

logger = logging.getLogger(__name__)

RRF_K = 60
FINGERPRINT_BOOST = 0.02

# Small nudge so ERROR/WARN lines rank above equally similar INFO lines.
LEVEL_BOOST = {
    "ERROR": 0.003,
    "FATAL": 0.003,
    "CRITICAL": 0.003,
    "WARN": 0.0015,
    "WARNING": 0.0015,
}
MAX_QUERY_CHARS = 4000
MAX_TOKENS = 40
CANDIDATES = 50
# Vector rows fetched per wanted chunk: one chunk can have several window vectors.
WINDOW_OVERFETCH = 8
_WORD = re.compile(r"\w+", re.UNICODE)

SOURCE_VECTOR = "vector"
SOURCE_FULLTEXT = "fulltext"
SOURCE_CODES = "error_code"
SOURCE_TRIGRAM = "trigram"


@dataclass(frozen=True)
class SearchFilters:
    service: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    document_id: int | None = None


@dataclass
class Hit:
    chunk_id: int
    text: str
    service: str | None
    level: str | None
    ts: datetime | None
    fingerprint: str | None
    score: float = 0.0
    sources: list[str] = field(default_factory=list)
    similarity: float | None = None
    fingerprint_match: bool = False


@dataclass(frozen=True)
class SearchResult:
    hits: list[Hit]
    degraded: bool
    inconclusive: bool
    reason: str
    query: str  # the redacted query


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_query(query: str) -> str:
    """Postgres text cannot hold NUL; other control characters are noise in a search query."""
    return _CONTROL.sub("", query)


def query_tokens(query: str) -> list[str]:
    seen: dict[str, None] = {}
    for token in _WORD.findall(query.lower()):
        if len(token) >= 2:
            seen.setdefault(token, None)
    return list(seen)[:MAX_TOKENS]


def rrf_merge(rankings: dict[str, list[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of 1 / (k + rank), rank from 1."""
    scores: dict[int, float] = {}
    for ids in rankings.values():
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def _filter_sql(filters: SearchFilters) -> str:
    parts = ""
    if filters.document_id is not None:
        parts += " AND c.document_id = CAST(:document_id AS bigint)"
    if filters.service is not None:
        parts += " AND c.service = CAST(:service AS text)"
    if filters.since is not None:
        parts += " AND c.ts >= CAST(:since AS timestamptz)"
    if filters.until is not None:
        parts += " AND c.ts <= CAST(:until AS timestamptz)"
    return parts


def _filter_params(filters: SearchFilters) -> dict[str, object]:
    params: dict[str, object] = {}
    if filters.document_id is not None:
        params["document_id"] = filters.document_id
    if filters.service is not None:
        params["service"] = filters.service
    if filters.since is not None:
        params["since"] = filters.since
    if filters.until is not None:
        params["until"] = filters.until
    return params


_COLUMNS = "c.id, c.text, c.service, c.level, c.ts, c.fingerprint"


async def _vector(
    engine: AsyncEngine, table: str, vec: list[float], filters: SearchFilters, limit: int
) -> list[tuple[int, float]]:
    if table not in ALLOWED_TABLES:
        raise ValueError(f"unknown embedding table: {table!r}")
    sql = text(
        f"WITH w AS ("
        f"SELECT e.chunk_id, e.embedding <=> CAST(:v AS vector) AS dist "
        f"FROM {table} e JOIN chunks c ON c.id = e.chunk_id "
        f"WHERE true{_filter_sql(filters)} "
        f"ORDER BY e.embedding <=> CAST(:v AS vector) LIMIT CAST(:m AS integer)) "
        f"SELECT chunk_id, 1 - min(dist) AS sim FROM w "
        f"GROUP BY chunk_id ORDER BY min(dist), chunk_id LIMIT CAST(:n AS integer)"
    )
    params = {
        "v": vector_literal(vec),
        "m": limit * WINDOW_OVERFETCH,
        "n": limit,
        **_filter_params(filters),
    }
    async with engine.connect() as conn:
        return [(int(r[0]), float(r[1])) for r in (await conn.execute(sql, params)).all()]


async def _fulltext(
    engine: AsyncEngine, tokens: list[str], filters: SearchFilters, limit: int
) -> list[int]:
    if not tokens:
        return []
    sql = text(
        f"SELECT c.id FROM chunks c "
        f"WHERE c.tsv @@ to_tsquery('simple', CAST(:q AS text)){_filter_sql(filters)} "
        f"ORDER BY ts_rank_cd(c.tsv, to_tsquery('simple', CAST(:q AS text))) DESC, c.id "
        f"LIMIT CAST(:n AS integer)"
    )
    query = " | ".join(f"'{t}'" for t in tokens if "'" not in t and "\\" not in t)
    if not query:
        return []
    params = {"q": query, "n": limit, **_filter_params(filters)}
    async with engine.connect() as conn:
        return [int(r[0]) for r in (await conn.execute(sql, params)).all()]


async def _codes(
    engine: AsyncEngine, codes: list[str], filters: SearchFilters, limit: int
) -> list[int]:
    if not codes:
        return []
    sql = text(
        f"SELECT c.id FROM chunks c "
        f"WHERE c.error_codes && CAST(:codes AS text[]){_filter_sql(filters)} "
        f"ORDER BY cardinality(ARRAY(SELECT unnest(c.error_codes) "
        f"INTERSECT SELECT unnest(CAST(:codes AS text[])))) DESC, c.id "
        f"LIMIT CAST(:n AS integer)"
    )
    params = {"codes": codes, "n": limit, **_filter_params(filters)}
    async with engine.connect() as conn:
        return [int(r[0]) for r in (await conn.execute(sql, params)).all()]


async def _trigram(
    engine: AsyncEngine, query: str, filters: SearchFilters, limit: int
) -> list[int]:
    sql = text(
        f"SELECT c.id FROM chunks c "
        f"WHERE CAST(:q AS text) <% c.text{_filter_sql(filters)} "
        f"ORDER BY word_similarity(CAST(:q AS text), c.text) DESC, c.id "
        f"LIMIT CAST(:n AS integer)"
    )
    params = {"q": query[:200], "n": limit, **_filter_params(filters)}
    async with engine.connect() as conn:
        return [int(r[0]) for r in (await conn.execute(sql, params)).all()]


async def _load(engine: AsyncEngine, ids: list[int]) -> dict[int, Hit]:
    if not ids:
        return {}
    sql = text(f"SELECT {_COLUMNS} FROM chunks c WHERE c.id = ANY(CAST(:ids AS bigint[]))")
    async with engine.connect() as conn:
        rows = (await conn.execute(sql, {"ids": ids})).all()
    return {int(r[0]): Hit(int(r[0]), str(r[1]), r[2], r[3], r[4], r[5]) for r in rows}


async def search(
    engine: AsyncEngine,
    provider: EmbeddingProvider | None,
    query: str,
    *,
    top_k: int = 8,
    min_similarity: float = 0.5,
    filters: SearchFilters | None = None,
    use_vector: bool = True,
    use_fulltext: bool = True,
) -> SearchResult:
    filters = filters or SearchFilters()
    redacted = Redactor().redact(_clean_query(query[:MAX_QUERY_CHARS])).strip()
    if not redacted:
        return SearchResult([], False, True, "empty query", "")

    fingerprint = make_fingerprint(redacted)
    codes = extract_error_codes(redacted)
    tokens = query_tokens(redacted)

    degraded = False
    similarities: dict[int, float] = {}
    rankings: dict[str, list[int]] = {}

    if use_vector:
        if provider is None:
            degraded = True
        else:
            try:
                vec = (await provider.embed([redacted]))[0]
                pairs = await _vector(engine, provider.table, vec, filters, CANDIDATES)
                similarities = dict(pairs)
                rankings[SOURCE_VECTOR] = [cid for cid, _ in pairs]
            except Exception as exc:  # any embedding or vector failure -> keyword-only
                logger.warning("vector search unavailable: %s", type(exc).__name__)
                degraded = True

    if use_fulltext:
        rankings[SOURCE_FULLTEXT] = await _fulltext(engine, tokens, filters, CANDIDATES)
        rankings[SOURCE_CODES] = await _codes(engine, codes, filters, CANDIDATES)
        rankings[SOURCE_TRIGRAM] = await _trigram(engine, redacted, filters, CANDIDATES)

    scores = rrf_merge(rankings)
    hits = await _load(engine, list(scores))
    for chunk_id, hit in hits.items():
        hit.score = scores[chunk_id]
        hit.sources = [name for name, ids in rankings.items() if chunk_id in ids]
        hit.similarity = similarities.get(chunk_id)
        if hit.fingerprint is not None and hit.fingerprint == fingerprint:
            hit.fingerprint_match = True
            hit.score += FINGERPRINT_BOOST
        hit.score += LEVEL_BOOST.get((hit.level or "").upper(), 0.0)

    passing = [h for h in hits.values() if _passes(h, min_similarity)]
    passing.sort(key=lambda h: (-h.score, h.chunk_id))
    top = passing[:top_k]
    if not top:
        reason = "no chunk matched an error code, fingerprint, keyword or similar vector"
        return SearchResult([], degraded, True, reason, redacted)
    return SearchResult(top, degraded, False, "ok", redacted)


def _passes(hit: Hit, min_similarity: float) -> bool:
    if hit.fingerprint_match:
        return True
    if SOURCE_CODES in hit.sources or SOURCE_FULLTEXT in hit.sources:
        return True
    return hit.similarity is not None and hit.similarity >= min_similarity
