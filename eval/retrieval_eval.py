"""Retrieval eval: hit-rate@k for vector-only, lexical-only and hybrid search.

Ingests every fixture file named by the cases into a scratch database, embeds the
chunks, then runs each query in three modes. A case is a hit in a mode when any of the
top-k chunk texts contains one of its evidence patterns (chunk ids change per ingest).

Modes, as search() exposes them:
- vector:  embeddings only
- lexical: full-text + error codes + trigram (search() has no full-text-only switch)
- hybrid:  everything, merged with RRF

The similarity threshold is switched off so the ranking is measured, not the threshold.
A random top-k baseline is reported per case so easy cases are visible.

Run from the repo root against a scratch database whose name ends in _eval:
    python -m eval.retrieval_eval
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.core.db import create_engine
from app.services.documents import store_document
from app.services.embedding_worker import ALLOWED_TABLES, embed_pending
from app.services.embeddings import EmbeddingError, EmbeddingProvider, make_provider
from app.services.retrieval import search
from eval.cases import CaseError, EvalCase, check_fixtures, load_cases

MODES: dict[str, tuple[bool, bool]] = {  # name -> (use_vector, use_fulltext)
    "vector": (True, False),
    "lexical": (False, True),
    "hybrid": (True, True),
}
NO_THRESHOLD = -1.0  # similarity = 1 - cosine distance, so it can be negative
SCRATCH_SUFFIX = "_eval"


def first_hit_rank(texts: Sequence[str], patterns: Sequence[str]) -> int | None:
    """1-based rank of the first text containing any pattern, or None."""
    for rank, body in enumerate(texts, start=1):
        if any(pattern in body for pattern in patterns):
            return rank
    return None


def random_baseline(total: int, matching: int, k: int) -> float:
    """Chance that k chunks drawn at random (no replacement) include a matching one."""
    if total <= 0 or matching <= 0:
        return 0.0
    k = min(k, total)
    if total - matching < k:
        return 1.0
    return 1.0 - math.comb(total - matching, k) / math.comb(total, k)


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A rank that is not None means a hit inside the top-k (search() never returns more)."""
    n = len(rows)
    modes: dict[str, dict[str, Any]] = {}
    for mode in MODES:
        hits = sum(1 for row in rows if row["ranks"][mode] is not None)
        modes[mode] = {"hits": hits, "cases": n, "rate": hits / n if n else 0.0}
    baseline = sum(row["baseline"] for row in rows) / n if n else 0.0
    return {"cases": n, "modes": modes, "random_baseline": baseline}


def render(summary: Mapping[str, Any], k: int) -> str:
    lines = [f"{'mode':<10} {'hits':>7} {f'hit-rate@{k}':>12}"]
    for mode, m in summary["modes"].items():
        lines.append(f"{mode:<10} {m['hits']:>3}/{m['cases']:<3} {m['rate']:>12.0%}")
    lines.append(f"random top-{k} baseline (mean over cases): {summary['random_baseline']:.0%}")
    return "\n".join(lines)


async def ingest(engine: AsyncEngine, root: Path, cases: Sequence[EvalCase]) -> None:
    for rel in sorted({c.file for c in cases if c.file}):
        path = root / rel
        doc = await store_document(engine, path.name, path.read_bytes())
        state = "already stored" if doc.duplicate else "stored"
        print(f"  {rel}: {doc.chunks} chunks, {state}")


async def embed_all(engine: AsyncEngine, provider: EmbeddingProvider) -> int:
    """Embed every chunk, then verify none was skipped (a skip would skew vector-only)."""
    if provider.table not in ALLOWED_TABLES:
        raise ValueError(f"unknown embedding table: {provider.table!r}")
    total = 0
    while True:
        stored = await embed_pending(engine, provider)
        if stored == 0:
            break
        total += stored
        print(f"  embedded {total} chunks", flush=True)
    missing_sql = text(
        f"SELECT count(*) FROM chunks c "
        f"WHERE NOT EXISTS (SELECT 1 FROM {provider.table} e WHERE e.chunk_id = c.id)"
    )
    async with engine.connect() as conn:
        missing = int((await conn.execute(missing_sql)).scalar_one())
    if missing:
        raise RuntimeError(f"{missing} chunks still have no embedding")
    return total


async def chunk_texts(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT text FROM chunks ORDER BY id"))).all()
    return [str(r[0]) for r in rows]


async def evaluate(
    engine: AsyncEngine,
    provider: EmbeddingProvider,
    cases: Sequence[EvalCase],
    corpus: Sequence[str],
    k: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        matching = sum(1 for body in corpus if any(p in body for p in case.evidence_patterns))
        if matching == 0:
            raise RuntimeError(f"{case.id}: no stored chunk contains its evidence patterns")
        ranks: dict[str, int | None] = {}
        for mode, (use_vector, use_fulltext) in MODES.items():
            result = await search(
                engine,
                provider,
                case.query,
                top_k=k,
                min_similarity=NO_THRESHOLD,
                use_vector=use_vector,
                use_fulltext=use_fulltext,
            )
            if use_vector and result.degraded:
                raise RuntimeError(f"{case.id}: vector search degraded in {mode} mode")
            ranks[mode] = first_hit_rank([h.text for h in result.hits], case.evidence_patterns)
        rows.append(
            {
                "id": case.id,
                "source": case.source,
                "matching_chunks": matching,
                "baseline": random_baseline(len(corpus), matching, k),
                "ranks": ranks,
            }
        )
        shown = "  ".join(f"{m}={'-' if r is None else r}" for m, r in ranks.items())
        print(f"  {case.id:<34} {shown}")
    return rows


async def run(cases_path: Path, out_path: Path, k: int) -> int:
    settings = get_settings()
    url = make_url(settings.database_url)
    if not (url.database or "").endswith(SCRATCH_SUFFIX):
        print(
            f"refusing to run: database {url.database!r} does not end with {SCRATCH_SUFFIX!r}. "
            "Point DATABASE_URL at a scratch database."
        )
        return 2
    print(
        f"database {url.host}:{url.port}/{url.database}, embeddings: {settings.embedding_provider}"
    )
    try:
        cases = [c for c in load_cases(cases_path) if not c.expect_inconclusive]
    except CaseError as exc:
        print(exc)
        return 1
    problems = check_fixtures(cases, cases_path.parent)
    if problems:
        print("\n".join(problems))
        return 1
    provider = make_provider(settings)
    if provider is None:
        print("no embedding provider available: set OPENAI_API_KEY with EMBEDDING_PROVIDER=openai")
        return 2

    engine = create_engine(settings)
    try:
        print("ingesting")
        await ingest(engine, cases_path.parent, cases)
        print("embedding")
        await embed_all(engine, provider)
        corpus = await chunk_texts(engine)
        print(f"scoring {len(cases)} cases over {len(corpus)} chunks (rank of first hit, - = miss)")
        rows = await evaluate(engine, provider, cases, corpus, k)
    except EmbeddingError as exc:
        print(f"embedding failed: {exc}")
        return 1
    finally:
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()
        await engine.dispose()

    summary = summarize(rows)
    print()
    print(render(summary, k))
    fake = settings.embedding_provider == "fake"
    if fake:
        print("NOT FOR PUBLICATION: fake embeddings")
    doc = {
        "run": uuid.uuid4().hex[:8],
        "created_at": datetime.now(UTC).isoformat(),
        "embedding_provider": settings.embedding_provider,
        "publishable": not fake,
        "top_k": k,
        "corpus_chunks": len(corpus),
        "summary": summary,
        "cases": rows,
    }
    await asyncio.to_thread(out_path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(out_path.write_text, json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieval eval: hit-rate@k per search mode")
    parser.add_argument("--cases", type=Path, default=Path("eval/fixtures/cases.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("eval/results/retrieval_eval.json"))
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")
    return asyncio.run(run(args.cases, args.out, args.top_k))


if __name__ == "__main__":
    raise SystemExit(main())
