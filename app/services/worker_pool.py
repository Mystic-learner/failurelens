"""Worker pool: drains the ingest queue, flushes idle streams, shuts down without loss.

- N workers call process_item() for queued batches. A failing batch is retried a few times
  with backoff, then counted as failed and logged WITHOUT its content. The shipper keeps a
  batch until the server reports committed_seq >= seq, so it can resend a failed one.
- A flusher releases the pending record of streams that have had no batch for idle_s, so the
  newest record becomes searchable even when the log goes quiet.
- stop(): close the queue (new batches get 503), wait until every accepted batch is
  processed, stop the workers, then flush every pending record. Safe to call twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.batch_worker import process_item
from app.services.ingest_queue import IngestItem, IngestQueue
from app.services.storage import commit_batch
from app.services.stream_processor import StreamProcessor

logger = logging.getLogger(__name__)

# jsonb 'null' is not SQL NULL, so the pending record is tested with jsonb_typeof.
_SQL_PENDING_IDS = text(
    "SELECT id FROM streams WHERE jsonb_typeof(parser_state->'parser'->'pending') = 'object'"
)
_SQL_LOCK_IDLE = text(
    """
    SELECT s.service, s.source_id, s.committed_seq, CAST(s.parser_state AS text) AS state
    FROM streams s
    WHERE s.id = CAST(:id AS bigint)
      AND jsonb_typeof(s.parser_state->'parser'->'pending') = 'object'
      AND COALESCE((SELECT max(b.created_at) FROM ingest_batches b WHERE b.stream_id = s.id),
                   s.created_at)
          <= now() - make_interval(secs => CAST(:idle AS double precision))
    FOR UPDATE OF s
    """
)
_SQL_SAVE = text(
    "UPDATE streams SET parser_state = CAST(CAST(:state AS text) AS jsonb) "
    "WHERE id = CAST(:id AS bigint)"
)


async def flush_stream(engine: AsyncEngine, stream_id: int, idle_s: float) -> bool:
    """Release one stream's pending record if the stream has been idle for idle_s."""
    async with engine.begin() as conn:
        row = (await conn.execute(_SQL_LOCK_IDLE, {"id": stream_id, "idle": idle_s})).first()
        if row is None:
            return False
        processor = StreamProcessor(row.source_id)
        processor.import_state(json.loads(row.state))
        chunks = processor.flush()
        await commit_batch(
            conn,
            stream_id=stream_id,
            service=row.service,
            idempotency_key=f"flush-{uuid.uuid4().hex}",
            seq=int(row.committed_seq),
            line_count=sum(c.chunk.line_count for c in chunks),
            chunks=chunks,
        )
        await conn.execute(
            _SQL_SAVE, {"id": stream_id, "state": json.dumps(processor.export_state())}
        )
        return True


async def flush_idle(engine: AsyncEngine, idle_s: float) -> int:
    """Flush every idle stream that has a pending record. Returns how many were flushed."""
    async with engine.connect() as conn:
        ids = [int(r[0]) for r in (await conn.execute(_SQL_PENDING_IDS)).all()]
    flushed = 0
    for stream_id in ids:
        if await flush_stream(engine, stream_id, idle_s):
            flushed += 1
    return flushed


class WorkerPool:
    def __init__(
        self,
        engine: AsyncEngine,
        queue: IngestQueue,
        workers: int,
        flush_interval_s: float,
        idle_s: float,
        retries: int = 3,
        retry_delay_s: float = 0.5,
    ) -> None:
        self._engine = engine
        self._queue = queue
        self._workers = workers
        self._flush_interval_s = flush_interval_s
        self._idle_s = idle_s
        self._retries = retries
        self._retry_delay_s = retry_delay_s
        self._tasks: list[asyncio.Task[None]] = []
        self._flusher: asyncio.Task[None] | None = None
        self._started = False
        self._stopped = False
        self.processed = 0
        self.failed = 0

    def start(self) -> None:
        if self._started or self._workers == 0:
            return
        self._started = True
        self._tasks = [asyncio.create_task(self._run()) for _ in range(self._workers)]
        self._flusher = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if not self._started:
            self._queue.close()
            return
        await self._queue.drain()
        await asyncio.gather(*self._tasks)
        if self._flusher is not None:
            self._flusher.cancel()
            await asyncio.gather(self._flusher, return_exceptions=True)
        try:
            await flush_idle(self._engine, 0)
        except Exception as exc:
            logger.error("final flush failed: %s", type(exc).__name__)

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            try:
                await self._handle(item)
            finally:
                self._queue.task_done()

    async def _handle(self, item: IngestItem) -> None:
        for attempt in range(self._retries):
            try:
                await process_item(self._engine, item)
                self.processed += 1
                return
            except Exception as exc:
                logger.warning(
                    "batch attempt %d failed (service=%s seq=%d): %s",
                    attempt + 1, item.service, item.seq, type(exc).__name__,
                )
                if attempt < self._retries - 1:
                    await asyncio.sleep(self._retry_delay_s * 2**attempt)
        self.failed += 1
        logger.error("batch dropped after retries (service=%s seq=%d)", item.service, item.seq)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval_s)
            try:
                await flush_idle(self._engine, self._idle_s)
            except Exception as exc:
                logger.warning("idle flush failed: %s", type(exc).__name__)