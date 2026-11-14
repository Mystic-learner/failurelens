"""Widen diagnosis evidence beyond similarity hits.

Similarity to the error line finds lines that look like the error. It cannot find
"what changed right before it broke", so we also read the same service's recent lines.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.chunking import SEVERITY
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


CROSS_WINDOW = timedelta(minutes=15)
CROSS_LIMIT = 3
_CROSS_FETCH_LIMIT = 400
# Name parts that say what kind of service it is, not which one.
_GENERIC_PARTS = frozenset(
    {"api", "svc", "service", "services", "worker", "web", "job", "app", "server", "prod"}
)

_SQL_SERVICES = text(
    "SELECT DISTINCT service FROM chunks "
    "WHERE service IS NOT NULL AND service <> :svc "
    "AND ts >= CAST(:since AS timestamptz) AND ts < CAST(:until AS timestamptz)"
)
_SQL_OTHER = text(
    "SELECT id, service, level, text FROM chunks "
    "WHERE service = ANY(CAST(:names AS text[])) "
    "AND ts >= CAST(:since AS timestamptz) AND ts < CAST(:until AS timestamptz) "
    "ORDER BY ts DESC, id DESC LIMIT :n"
)


def _parts(service: str) -> list[str]:
    """Lowercase name parts, without instance or run ids (any part containing a digit)."""
    parts = re.split(r"[^a-z0-9]+", service.lower())
    return [p for p in parts if p and not any(ch.isdigit() for ch in p)]


def mentions_service(error_text: str, service: str) -> bool:
    """True if the error text contains a distinctive part of the service's name."""
    low = error_text.lower()
    names = [p for p in _parts(service) if len(p) >= 4 and p not in _GENERIC_PARTS]
    return any(re.search(r"\b" + re.escape(n), low) for n in names)


def related_services(error_text: str, own: str, names: Sequence[str]) -> list[str]:
    """Other services the error names. Instances of the same service are not 'other'."""
    mine = _parts(own)
    return [n for n in names if _parts(n) != mine and mentions_service(error_text, n)]


def pick_cross_evidence(
    rows: Sequence[tuple[int, str, str | None, str]],
) -> list[EvidenceChunk]:
    """rows are (id, service, level, text), newest first.

    Keeps lines that are ERROR or worse, or that their service logged only once in the
    window (a change event, not routine traffic). Each distinct line is kept once, and the
    service name goes on its own first line so quoted evidence still matches a whole line.
    """
    seen_in_service = Counter((svc, normalize(body)) for _, svc, _, body in rows)
    seen: set[str] = set()
    out: list[EvidenceChunk] = []
    for chunk_id, svc, level, body in rows:
        key = normalize(body)
        if key in seen:
            continue
        loud = SEVERITY.get(str(level), 0) >= SEVERITY["ERROR"]
        if not loud and seen_in_service[(svc, key)] > 1:
            continue
        seen.add(key)
        out.append(EvidenceChunk(str(chunk_id), f"SERVICE: {svc}\n{body}"))
        if len(out) == CROSS_LIMIT:
            break
    return out


async def fetch_other_services(
    engine: AsyncEngine,
    service: str,
    anchor: datetime,
    error_text: str,
) -> list[EvidenceChunk]:
    """Lines from other services that the error names, from the window before `anchor`."""
    since = anchor - CROSS_WINDOW
    async with engine.connect() as conn:
        found = (
            await conn.execute(_SQL_SERVICES, {"svc": service, "since": since, "until": anchor})
        ).all()
        related = related_services(error_text, service, [str(r[0]) for r in found])
        if not related:
            return []
        rows = (
            await conn.execute(
                _SQL_OTHER,
                {"names": related, "since": since, "until": anchor, "n": _CROSS_FETCH_LIMIT},
            )
        ).all()
    return pick_cross_evidence(
        [(int(r[0]), str(r[1]), None if r[2] is None else str(r[2]), str(r[3])) for r in rows]
    )
