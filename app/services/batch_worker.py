"""Process one queued batch: bytes in, chunks and signatures committed, state saved.

Everything for one batch happens in ONE transaction:
  1. upsert the stream row (this locks it) and SELECT ... FOR UPDATE
  2. duplicate idempotency key -> return, nothing changes
  3. restore the stream state (pending record, IP map) saved by the previous batch
  4. decode, parse, redact, chunk, fingerprint
  5. commit_batch (chunks, signatures, minute buckets, batch row, committed_seq)
  6. save the new stream state

A crash anywhere rolls back all of it, including the state, so a replay starts from
exactly the same state and loses or duplicates nothing. Batches of the same stream
serialize on the row lock (the pending record needs that); other streams run in parallel.

Binary-looking input is recorded as a committed batch with no chunks and no state change,
so the shipper stops retrying it. Sequence problems are reported, never rejected.
Sequences are assumed to start at 1.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.detector import DEFAULT_DETECTOR, DetectorConfig, detect
from app.services.ingest_queue import IngestItem
from app.services.parsing import BinaryInputError
from app.services.storage import CommitResult, commit_batch, get_or_create_stream
from app.services.stream_processor import StreamProcessor

_SQL_LOAD = text(
    "SELECT committed_seq, CAST(parser_state AS text) AS state "
    "FROM streams WHERE id = CAST(:id AS bigint) FOR UPDATE"
)
_SQL_SEEN = text(
    "SELECT 1 FROM ingest_batches "
    "WHERE stream_id = CAST(:id AS bigint) AND idempotency_key = CAST(:key AS text)"
)
_SQL_SAVE = text(
    "UPDATE streams SET parser_state = CAST(CAST(:state AS text) AS jsonb) "
    "WHERE id = CAST(:id AS bigint)"
)

Status = Literal["committed", "duplicate", "rejected_binary"]
Sequence = Literal["in_order", "gap", "late"]


@dataclass(frozen=True)
class BatchOutcome:
    status: Status
    sequence: Sequence
    commit: CommitResult | None
    incidents: tuple[int, ...] = ()


def sequence_status(committed_seq: int, seq: int) -> Sequence:
    if seq == committed_seq + 1:
        return "in_order"
    if seq > committed_seq + 1:
        return "gap"
    return "late"


async def process_item(
    engine: AsyncEngine,
    item: IngestItem,
    now: datetime | None = None,
    detector: DetectorConfig | None = DEFAULT_DETECTOR,
) -> BatchOutcome:
    async with engine.begin() as conn:
        stream_id = await get_or_create_stream(conn, item.service, item.source_id)
        row = (await conn.execute(_SQL_LOAD, {"id": stream_id})).one()
        sequence = sequence_status(int(row.committed_seq), item.seq)

        seen = await conn.execute(_SQL_SEEN, {"id": stream_id, "key": item.idempotency_key})
        if seen.first() is not None:
            return BatchOutcome("duplicate", sequence, None)

        processor = StreamProcessor(item.source_id)
        processor.import_state(json.loads(row.state) if row.state else {})
        try:
            chunks = processor.feed_bytes(item.data)
        except BinaryInputError:
            await commit_batch(
                conn,
                stream_id=stream_id,
                service=item.service,
                idempotency_key=item.idempotency_key,
                seq=item.seq,
                line_count=0,
                chunks=[],
                now=now,
            )
            return BatchOutcome("rejected_binary", sequence, None)

        result = await commit_batch(
            conn,
            stream_id=stream_id,
            service=item.service,
            idempotency_key=item.idempotency_key,
            seq=item.seq,
            line_count=processor.stats.lines_in,
            chunks=chunks,
            now=now,
        )
        await conn.execute(
            _SQL_SAVE, {"id": stream_id, "state": json.dumps(processor.export_state())}
        )
        opened: list[int] = []
        if detector is not None:
            opened = await detect(conn, result.signatures, now or datetime.now(UTC), detector)
        return BatchOutcome("committed", sequence, result, tuple(opened))
