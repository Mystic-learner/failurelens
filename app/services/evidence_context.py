"""Widen diagnosis evidence beyond similarity hits.

Similarity to the error line finds lines that look like the error. It cannot find
"what changed right before it broke", so we also read the same service's recent lines.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.diagnosis import EvidenceChunk

CONTEXT_WINDOW = timedelta(minutes=30)
CONTEXT_LIMIT = 4
_FETCH_LIMIT = 40

_SQL_BEFORE = text(
    "SELECT id, text FROM chunks "
    "WHERE service = :svc "
    "AND ts >= CAST(:since AS timestamptz) AND ts < CAST(:until AS timestamptz) "
    "AND id <> CAST(:ex AS bigint) "
    "AND fingerprint IS DISTINCT FROM CAST(:fp AS text) "
    "ORDER BY ts DESC, id DESC LIMIT :n"
)


def _strip_ts(line: str) -> str:
    parts = line.split(None, 2)
    if len(parts) == 3 and len(parts[0]) == 10 and parts[0][4] == "-" and parts[0][7] == "-":
        return parts[2]
    return line.strip()


def normalize(chunk_text: str) -> str:
    """Chunk text without timestamps, so repeats of one message compare equal."""
    return "\n".join(_strip_ts(ln) for ln in chunk_text.splitlines() if ln.strip())


def collapse_repeats(chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
    seen: set[str] = set()
    out: list[EvidenceChunk] = []
    for c in chunks:
        key = normalize(c.text)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def merge_context(chunks: list[EvidenceChunk], extra: list[EvidenceChunk]) -> list[EvidenceChunk]:
    out = list(chunks)
    ids = {c.chunk_id for c in out}
    seen = {normalize(c.text) for c in out}
    for c in extra:
        key = normalize(c.text)
        if c.chunk_id in ids or key in seen:
            continue
        ids.add(c.chunk_id)
        seen.add(key)
        out.append(c)
    return out


async def fetch_before(
    engine: AsyncEngine,
    service: str,
    anchor: datetime,
    exemplar_id: int | None,
    fingerprint: str | None,
) -> list[EvidenceChunk]:
    """Same-service chunks from the window before `anchor`, newest first, repeats removed."""
    params = {
        "svc": service,
        "since": anchor - CONTEXT_WINDOW,
        "until": anchor,
        "ex": exemplar_id if exemplar_id is not None else -1,
        "fp": fingerprint,
        "n": _FETCH_LIMIT,
    }
    async with engine.connect() as conn:
        rows = (await conn.execute(_SQL_BEFORE, params)).all()
    found = [EvidenceChunk(str(r[0]), str(r[1])) for r in rows]
    return collapse_repeats(found)[:CONTEXT_LIMIT]
