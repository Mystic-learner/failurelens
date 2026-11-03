from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services import batch_worker
from app.services.batch_worker import process_item
from app.services.ingest_queue import IngestItem
from tests.storagetools import count, fetch_value

pytestmark = pytest.mark.asyncio

PART_1 = (
    "2024-01-01 10:00:00 ERROR request failed\n"
    "Traceback (most recent call last):\n"
    '  File "/app/a.py", line 10, in handler\n'
)
PART_2 = "    do()\nValueError: bad 5\n2024-01-01 10:00:01 INFO next\n"
ERR_THEN_INFO = (
    "2024-01-01 10:00:00 ERROR payment 1 failed\n"
    "2024-01-01 10:00:05 INFO ok\n"
)


def item(
    data: bytes | str,
    seq: int = 1,
    key: str = "k1",
    service: str = "svc",
    source: str = "src",
) -> IngestItem:
    raw = data.encode() if isinstance(data, str) else data
    return IngestItem(service, source, key, seq, raw)


async def test_stack_trace_split_across_two_batches_is_stored_whole(engine: AsyncEngine) -> None:
    first = await process_item(engine, item(PART_1, 1, "k1"))
    assert first.status == "committed"
    assert await count(engine, "chunks") == 0  # the trace is still pending
    state = await fetch_value(engine, "SELECT CAST(parser_state AS text) FROM streams")
    assert "Traceback" in state

    second = await process_item(engine, item(PART_2, 2, "k2"))
    assert second.status == "committed"
    assert second.sequence == "in_order"
    assert await count(engine, "chunks") == 1
    stored = await fetch_value(engine, "SELECT text FROM chunks")
    assert stored == PART_1 + "    do()\nValueError: bad 5"
    assert await fetch_value(engine, "SELECT level FROM chunks") == "ERROR"
    assert await fetch_value(engine, "SELECT count FROM signatures") == 1


async def test_duplicate_key_changes_nothing(engine: AsyncEngine) -> None:
    await process_item(engine, item(ERR_THEN_INFO, 1, "k1"))
    state_before = await fetch_value(engine, "SELECT CAST(parser_state AS text) FROM streams")
    again = await process_item(engine, item(ERR_THEN_INFO, 1, "k1"))

    assert again.status == "duplicate"
    assert again.commit is None
    assert await count(engine, "ingest_batches") == 1
    assert await count(engine, "chunks") == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 1
    state_after = await fetch_value(engine, "SELECT CAST(parser_state AS text) FROM streams")
    assert state_after == state_before


async def test_binary_batch_is_recorded_and_rejected(engine: AsyncEngine) -> None:
    junk = b"\x00\x01\x02" * 3000
    out = await process_item(engine, item(junk, 1, "k1"))
    assert out.status == "rejected_binary"
    assert await count(engine, "ingest_batches") == 1
    assert await count(engine, "chunks") == 0
    assert await fetch_value(engine, "SELECT committed_seq FROM streams") == 1
    again = await process_item(engine, item(junk, 1, "k1"))
    assert again.status == "duplicate"


async def test_crash_mid_batch_rolls_back_and_replay_succeeds(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = batch_worker.commit_batch

    async def crash_after_commit(*args: Any, **kwargs: Any) -> Any:
        await real(*args, **kwargs)
        raise RuntimeError("worker crashed before the transaction committed")

    monkeypatch.setattr(batch_worker, "commit_batch", crash_after_commit)
    with pytest.raises(RuntimeError):
        await process_item(engine, item(ERR_THEN_INFO, 1, "k1"))
    for table in ("streams", "ingest_batches", "chunks", "signatures", "signature_buckets"):
        assert await count(engine, table) == 0

    monkeypatch.undo()
    replay = await process_item(engine, item(ERR_THEN_INFO, 1, "k1"))
    assert replay.status == "committed"
    assert await count(engine, "chunks") == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 1
    assert await count(engine, "ingest_batches") == 1


async def test_sequence_gaps_and_late_batches_are_reported_not_rejected(
    engine: AsyncEngine,
) -> None:
    one = await process_item(engine, item("2024-01-01 10:00:00 INFO a\n", 1, "k1"))
    three = await process_item(engine, item("2024-01-01 10:00:01 INFO b\n", 3, "k3"))
    two = await process_item(engine, item("2024-01-01 10:00:02 INFO c\n", 2, "k2"))

    assert (one.status, one.sequence) == ("committed", "in_order")
    assert (three.status, three.sequence) == ("committed", "gap")
    assert (two.status, two.sequence) == ("committed", "late")
    assert await fetch_value(engine, "SELECT committed_seq FROM streams") == 3
    assert await count(engine, "ingest_batches") == 3


async def test_same_error_in_two_services_is_two_signatures(engine: AsyncEngine) -> None:
    await process_item(engine, item(ERR_THEN_INFO, 1, "k1", "svc-a", "src-a"))
    await process_item(engine, item(ERR_THEN_INFO, 1, "k1", "svc-b", "src-b"))

    assert await count(engine, "streams") == 2
    assert await count(engine, "signatures") == 2
    assert await fetch_value(engine, "SELECT count(DISTINCT service) FROM signatures") == 2
    assert await count(engine, "chunks") == 2


async def test_20_concurrent_batches_on_one_stream(engine: AsyncEngine) -> None:
    def data(i: int) -> str:
        return (
            f"2024-01-01 10:{i:02d}:00 ERROR payment {i} failed\n"
            f"2024-01-01 10:{i:02d}:05 INFO done {i}\n"
        )

    outcomes = await asyncio.gather(
        *(process_item(engine, item(data(i), i, f"k{i}")) for i in range(1, 21))
    )

    assert all(o.status == "committed" for o in outcomes)
    assert await count(engine, "ingest_batches") == 20
    assert await count(engine, "streams") == 1
    assert await fetch_value(engine, "SELECT committed_seq FROM streams") == 20
    assert await fetch_value(engine, "SELECT count(*) FROM chunks WHERE level = 'ERROR'") == 20
    assert await count(engine, "signatures") == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 20