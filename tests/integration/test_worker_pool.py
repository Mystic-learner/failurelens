from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services import worker_pool
from app.services.batch_worker import process_item
from app.services.ingest_queue import IngestItem, IngestQueue
from app.services.worker_pool import WorkerPool, flush_idle
from tests.appclient import make_settings, running_client
from tests.storagetools import count, fetch_value

pytestmark = pytest.mark.asyncio

PART_1 = (
    "2024-01-01 10:00:00 ERROR request failed\n"
    "Traceback (most recent call last):\n"
    '  File "/app/a.py", line 10, in handler\n'
)
PENDING_SQL = "SELECT jsonb_typeof(parser_state->'parser'->'pending') FROM streams"
REAL_BATCHES = "SELECT count(*) FROM ingest_batches WHERE idempotency_key NOT LIKE 'flush-%'"
FLUSH_BATCHES = "SELECT count(*) FROM ingest_batches WHERE idempotency_key LIKE 'flush-%'"


def item(data: str, seq: int, key: str | None = None, service: str = "svc") -> IngestItem:
    return IngestItem(service, "src", key or f"k{seq}", seq, data.encode())


def error_line(i: int) -> str:
    return f"2024-01-01 10:{i % 60:02d}:00 ERROR payment {i} failed\n"


def make_pool(engine: AsyncEngine, queue: IngestQueue, workers: int = 2) -> WorkerPool:
    return WorkerPool(
        engine, queue, workers, flush_interval_s=60, idle_s=60, retries=3, retry_delay_s=0.01
    )


async def test_idle_flush_releases_the_pending_record(engine: AsyncEngine) -> None:
    await process_item(engine, item(PART_1, 1))
    assert await count(engine, "chunks") == 0

    assert await flush_idle(engine, 0) == 1
    assert await count(engine, "chunks") == 1
    assert await fetch_value(engine, "SELECT text FROM chunks") == PART_1.rstrip("\n")
    assert await fetch_value(engine, "SELECT level FROM chunks") == "ERROR"
    assert await fetch_value(engine, "SELECT count FROM signatures") == 1
    assert await fetch_value(engine, PENDING_SQL) == "null"

    assert await flush_idle(engine, 0) == 0  # nothing pending any more
    assert await count(engine, "chunks") == 1


async def test_a_stream_that_is_not_idle_is_not_flushed(engine: AsyncEngine) -> None:
    await process_item(engine, item(PART_1, 1))
    assert await flush_idle(engine, 3600) == 0
    assert await count(engine, "chunks") == 0
    assert await fetch_value(engine, PENDING_SQL) == "object"


async def test_pool_processes_everything_and_flushes_on_stop(engine: AsyncEngine) -> None:
    queue = IngestQueue(max_items=100, max_bytes=10_000_000)
    pool = make_pool(engine, queue)
    pool.start()
    for i in range(1, 11):
        queue.put(item(error_line(i), i))
    await pool.stop()

    assert pool.processed == 10
    assert pool.failed == 0
    assert await fetch_value(engine, REAL_BATCHES) == 10
    assert await fetch_value(engine, FLUSH_BATCHES) == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 10
    assert await fetch_value(engine, PENDING_SQL) == "null"


async def test_shutdown_drains_30_queued_batches_without_loss(engine: AsyncEngine) -> None:
    queue = IngestQueue(max_items=100, max_bytes=10_000_000)
    pool = make_pool(engine, queue, workers=4)
    pool.start()
    for i in range(1, 31):
        queue.put(item(error_line(i), i))
    await pool.stop()  # called immediately: must still finish all 30

    assert await fetch_value(engine, REAL_BATCHES) == 30
    assert await fetch_value(engine, FLUSH_BATCHES) == 1
    assert await fetch_value(engine, "SELECT count(*) FROM chunks WHERE level = 'ERROR'") == 30
    assert await fetch_value(engine, "SELECT count FROM signatures") == 30
    assert queue.depth == 0
    assert queue.closed


async def test_stop_twice_is_safe_and_workerless_pool_just_closes(engine: AsyncEngine) -> None:
    queue = IngestQueue(max_items=10, max_bytes=1000)
    pool = make_pool(engine, queue)
    pool.start()
    await pool.stop()
    await pool.stop()

    idle_queue = IngestQueue(max_items=10, max_bytes=1000)
    idle_pool = make_pool(engine, idle_queue, workers=0)
    idle_pool.start()
    await idle_pool.stop()
    assert idle_queue.closed


async def test_failing_batch_is_retried_then_dropped_and_others_continue(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = process_item
    attempts: dict[str, int] = {}

    async def flaky(eng: AsyncEngine, it: IngestItem, now: Any = None) -> Any:
        attempts[it.idempotency_key] = attempts.get(it.idempotency_key, 0) + 1
        if it.idempotency_key == "bad":
            raise RuntimeError("database exploded")
        return await real(eng, it, now)

    monkeypatch.setattr(worker_pool, "process_item", flaky)
    queue = IngestQueue(max_items=100, max_bytes=10_000_000)
    pool = make_pool(engine, queue)
    pool.start()
    queue.put(item(error_line(1), 1, "bad"))
    for i in range(2, 6):
        queue.put(item(error_line(i), i))
    await pool.stop()

    assert attempts["bad"] == 3
    assert pool.failed == 1
    assert pool.processed == 4
    assert await fetch_value(engine, REAL_BATCHES) == 4


async def test_a_transient_failure_succeeds_on_retry(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = process_item
    calls = {"n": 0}

    async def fails_once(eng: AsyncEngine, it: IngestItem, now: Any = None) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection reset")
        return await real(eng, it, now)

    monkeypatch.setattr(worker_pool, "process_item", fails_once)
    queue = IngestQueue(max_items=10, max_bytes=1_000_000)
    pool = make_pool(engine, queue, workers=1)
    pool.start()
    queue.put(item(error_line(1), 1))
    await pool.stop()

    assert pool.processed == 1
    assert pool.failed == 0
    assert await fetch_value(engine, REAL_BATCHES) == 1


async def test_50_concurrent_http_batches_end_to_end(
    migrated_db_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(
        database_url=migrated_db_url,
        worker_count=3,
        worker_flush_interval_s=60,
        stream_idle_s=60,
    )
    async with running_client(settings) as client:
        async def send(i: int) -> httpx.Response:
            return await client.post(
                "/ingest/stream",
                content=error_line(i).encode(),
                headers={
                    "X-Service": "svc",
                    "X-Source-Id": "src",
                    "Idempotency-Key": f"k{i}",
                    "X-Sequence": str(i),
                },
            )

        responses = await asyncio.gather(*(send(i) for i in range(1, 51)))
        assert all(r.status_code == 202 for r in responses)
        await client.app.state.pool.stop()  # type: ignore[attr-defined]

    assert await fetch_value(engine, REAL_BATCHES) == 50
    assert await fetch_value(engine, FLUSH_BATCHES) == 1
    assert await count(engine, "streams") == 1
    assert await fetch_value(engine, "SELECT count(*) FROM chunks WHERE level = 'ERROR'") == 50
    assert await count(engine, "signatures") == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 50
    assert await fetch_value(engine, "SELECT committed_seq FROM streams") == 50