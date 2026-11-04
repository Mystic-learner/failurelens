"""Storage layer: commit one processed batch to Postgres in ONE transaction.

The caller owns the transaction (``async with engine.begin() as conn``), so a crash
anywhere rolls back the batch record, the chunks, the signature counters and the stream
position together. Replaying the same (stream, idempotency_key) is then a no-op.

Rules:
- Exactly once per idempotency key: the batch row is claimed first with
  INSERT ... ON CONFLICT DO NOTHING. A concurrent writer with the same key waits for the
  first one and then sees a duplicate.
- Chunks are stored once (UNIQUE chunk_hash, ON CONFLICT DO NOTHING).
- Signature counters count OCCURRENCES in each accepted batch, even when the chunk text
  was already stored (1,000 identical timestamp-less errors still count 1,000). A batch
  replayed under a NEW key counts again: the idempotency key is what prevents double
  counting.
- "New signature" is decided by a separate INSERT ... ON CONFLICT DO NOTHING RETURNING,
  so under concurrency exactly one writer sees is_new=True.
- Rows are written in sorted order (chunk_hash, fingerprint, signature_id/minute), so
  concurrent batches take row locks in the same order and cannot deadlock each other.
- Occurrence time is the chunk's own timestamp, unless it is missing or flagged
  future_timestamp; then it is the server time (``now``). Minute buckets use that time.
- A signature's level only ever moves up (WARN -> ERROR), never down.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.services.chunking import SEVERITY
from app.services.stream_processor import ProcessedChunk

_ROWS = "CAST(CAST(:rows AS text) AS jsonb)"
_LEVELS = "CAST(ARRAY['UNKNOWN','DEBUG','INFO','WARN','ERROR','FATAL'] AS text[])"

_SQL_STREAM = text(
    """
    INSERT INTO streams (service, source_id) VALUES (:service, :source_id)
    ON CONFLICT (service, source_id) DO UPDATE SET service = EXCLUDED.service
    RETURNING id
    """
)

_SQL_CLAIM_BATCH = text(
    """
    INSERT INTO ingest_batches (stream_id, idempotency_key, seq, line_count)
    VALUES (CAST(:stream_id AS bigint), CAST(:key AS text), CAST(:seq AS bigint),
            CAST(:line_count AS integer))
    ON CONFLICT (stream_id, idempotency_key) DO NOTHING
    RETURNING id
    """
)

_SQL_INSERT_CHUNKS = text(
    f"""
    INSERT INTO chunks (stream_id, text, chunk_hash, fingerprint, level, ts, service, error_codes)
    SELECT CAST(:stream_id AS bigint), r.body, r.chunk_hash, r.fingerprint, r.level, r.ts,
           CAST(:service AS text), ARRAY(SELECT jsonb_array_elements_text(r.error_codes))
    FROM jsonb_to_recordset({_ROWS})
         AS r(body text, chunk_hash text, fingerprint text, level text, ts timestamptz,
              error_codes jsonb)
    ORDER BY r.chunk_hash
    ON CONFLICT (chunk_hash) DO NOTHING
    RETURNING id, chunk_hash
    """
)

_SQL_NEW_SIGNATURES = text(
    f"""
    INSERT INTO signatures (service, fingerprint, level, count, first_seen, last_seen)
    SELECT CAST(:service AS text), r.fingerprint, r.level, 0, r.first_seen, r.last_seen
    FROM jsonb_to_recordset({_ROWS})
         AS r(fingerprint text, level text, first_seen timestamptz, last_seen timestamptz)
    ORDER BY r.fingerprint
    ON CONFLICT (service, fingerprint) DO NOTHING
    RETURNING fingerprint
    """
)

_SQL_UPSERT_SIGNATURES = text(
    f"""
    INSERT INTO signatures
        (service, fingerprint, exemplar_chunk_id, level, count, first_seen, last_seen)
    SELECT CAST(:service AS text), r.fingerprint, r.exemplar_id, r.level, r.n,
           r.first_seen, r.last_seen
    FROM jsonb_to_recordset({_ROWS})
         AS r(fingerprint text, level text, n bigint, first_seen timestamptz,
              last_seen timestamptz, exemplar_id bigint)
    ORDER BY r.fingerprint
    ON CONFLICT (service, fingerprint) DO UPDATE SET
        count = signatures.count + EXCLUDED.count,
        first_seen = LEAST(signatures.first_seen, EXCLUDED.first_seen),
        last_seen = GREATEST(signatures.last_seen, EXCLUDED.last_seen),
        exemplar_chunk_id = COALESCE(signatures.exemplar_chunk_id, EXCLUDED.exemplar_chunk_id),
        level = CASE
            WHEN COALESCE(array_position({_LEVELS}, EXCLUDED.level), 0)
               > COALESCE(array_position({_LEVELS}, signatures.level), 0)
            THEN EXCLUDED.level ELSE signatures.level END
    RETURNING signatures.id, signatures.fingerprint, signatures.count AS total_count
    """
)

_SQL_BUCKETS = text(
    f"""
    INSERT INTO signature_buckets (signature_id, minute, count)
    SELECT r.signature_id, r.minute, r.n
    FROM jsonb_to_recordset({_ROWS})
         AS r(signature_id bigint, minute timestamptz, n bigint)
    ORDER BY r.signature_id, r.minute
    ON CONFLICT (signature_id, minute) DO UPDATE SET
        count = signature_buckets.count + EXCLUDED.count
    """
)

_SQL_STREAM_SEQ = text(
    """
    UPDATE streams SET committed_seq = GREATEST(committed_seq, CAST(:seq AS bigint))
    WHERE id = CAST(:stream_id AS bigint)
    """
)


@dataclass(frozen=True)
class SignatureHit:
    signature_id: int
    fingerprint: str
    is_new: bool  # first time this (service, fingerprint) was ever seen
    total_count: int  # lifetime occurrences after this batch
    batch_count: int  # occurrences added by this batch


@dataclass(frozen=True)
class CommitResult:
    duplicate: bool  # True: this idempotency key was already committed, nothing was written
    chunks_received: int
    chunks_inserted: int  # chunks that were not stored before
    signatures: tuple[SignatureHit, ...]


@dataclass
class _Group:
    level: str
    first: datetime
    last: datetime
    exemplar_id: int | None
    count: int = 0
    buckets: dict[datetime, int] = field(default_factory=dict)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _event_time(pc: ProcessedChunk, now: datetime) -> datetime:
    chunk = pc.chunk
    if chunk.first_ts is not None and "future_timestamp" not in chunk.flags:
        return chunk.first_ts.astimezone(UTC)
    return now


async def get_or_create_stream(conn: AsyncConnection, service: str, source_id: str) -> int:
    result = await conn.execute(_SQL_STREAM, {"service": service, "source_id": source_id})
    return int(result.scalar_one())


async def commit_batch(
    conn: AsyncConnection,
    *,
    stream_id: int,
    service: str,
    idempotency_key: str,
    seq: int,
    line_count: int,
    chunks: list[ProcessedChunk],
    now: datetime | None = None,
) -> CommitResult:
    if seq < 0 or line_count < 0:
        raise ValueError("seq and line_count must be >= 0")
    when = (now or datetime.now(UTC)).astimezone(UTC)

    claimed = (
        await conn.execute(
            _SQL_CLAIM_BATCH,
            {
                "stream_id": stream_id,
                "key": idempotency_key,
                "seq": seq,
                "line_count": line_count,
            },
        )
    ).first()
    if claimed is None:
        return CommitResult(True, len(chunks), 0, ())

    inserted = await _insert_chunks(conn, stream_id, service, chunks)
    hits = await _record_signatures(conn, service, chunks, inserted, when)
    await conn.execute(_SQL_STREAM_SEQ, {"seq": seq, "stream_id": stream_id})
    return CommitResult(False, len(chunks), len(inserted), hits)


async def _insert_chunks(
    conn: AsyncConnection, stream_id: int, service: str, chunks: list[ProcessedChunk]
) -> dict[str, int]:
    unique: dict[str, ProcessedChunk] = {}
    for pc in chunks:
        unique.setdefault(pc.chunk.chunk_hash, pc)
    if not unique:
        return {}
    rows = [
        {
            "body": pc.chunk.text.replace("\x00", ""),  # jsonb and TEXT cannot hold NUL
            "chunk_hash": chunk_hash,
            "fingerprint": pc.fingerprint,
            "level": pc.chunk.level,
            "ts": _iso(pc.chunk.first_ts) if pc.chunk.first_ts is not None else None,
            "error_codes": list(pc.error_codes),
        }
        for chunk_hash, pc in sorted(unique.items())
    ]
    result = await conn.execute(
        _SQL_INSERT_CHUNKS,
        {"stream_id": stream_id, "service": service, "rows": json.dumps(rows)},
    )
    return {str(r["chunk_hash"]): int(r["id"]) for r in result.mappings().all()}


async def _record_signatures(
    conn: AsyncConnection,
    service: str,
    chunks: list[ProcessedChunk],
    inserted: dict[str, int],
    now: datetime,
) -> tuple[SignatureHit, ...]:
    groups: dict[str, _Group] = {}
    for pc in chunks:
        fingerprint = pc.fingerprint
        if fingerprint is None:
            continue
        at = _event_time(pc, now)
        group = groups.get(fingerprint)
        if group is None:
            group = _Group(level=pc.chunk.level, first=at, last=at, exemplar_id=None)
            groups[fingerprint] = group
        group.count += 1
        if SEVERITY.get(pc.chunk.level, 0) > SEVERITY.get(group.level, 0):
            group.level = pc.chunk.level
        group.first = min(group.first, at)
        group.last = max(group.last, at)
        minute = at.replace(second=0, microsecond=0)
        group.buckets[minute] = group.buckets.get(minute, 0) + 1
        if group.exemplar_id is None:
            group.exemplar_id = inserted.get(pc.chunk.chunk_hash)
    if not groups:
        return ()

    rows = [
        {
            "fingerprint": fingerprint,
            "level": group.level,
            "n": group.count,
            "first_seen": _iso(group.first),
            "last_seen": _iso(group.last),
            "exemplar_id": group.exemplar_id,
        }
        for fingerprint, group in sorted(groups.items())
    ]
    params = {"service": service, "rows": json.dumps(rows)}

    new_rows = (await conn.execute(_SQL_NEW_SIGNATURES, params)).mappings().all()
    new_fingerprints = {str(r["fingerprint"]) for r in new_rows}
    upserted = (await conn.execute(_SQL_UPSERT_SIGNATURES, params)).mappings().all()

    ids = {str(r["fingerprint"]): int(r["id"]) for r in upserted}
    bucket_rows = sorted(
        (ids[fingerprint], minute, n)
        for fingerprint, group in groups.items()
        for minute, n in group.buckets.items()
    )
    await conn.execute(
        _SQL_BUCKETS,
        {
            "rows": json.dumps(
                [
                    {"signature_id": sid, "minute": _iso(minute), "n": n}
                    for sid, minute, n in bucket_rows
                ]
            )
        },
    )
    return tuple(
        SignatureHit(
            signature_id=int(r["id"]),
            fingerprint=str(r["fingerprint"]),
            is_new=str(r["fingerprint"]) in new_fingerprints,
            total_count=int(r["total_count"]),
            batch_count=groups[str(r["fingerprint"])].count,
        )
        for r in sorted(upserted, key=lambda row: str(row["fingerprint"]))
    )
