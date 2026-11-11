from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.storagetools import commit, count, fetch_value, make_chunks, setup_stream

pytestmark = pytest.mark.asyncio


async def test_50_concurrent_batches_on_one_stream(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)

    def raw_for(i: int) -> str:
        lines = [
            f"2024-01-01 10:{i - 1:02d}:{j:02d} ERROR payment {i * 100 + j} failed"
            for j in range(20)
        ]
        return "\n".join(lines) + "\n"

    batches = {i: make_chunks(raw_for(i)) for i in range(1, 51)}
    results = await asyncio.gather(
        *(commit(engine, sid, f"k{i}", i, batches[i]) for i in range(1, 51))
    )

    assert all(not r.duplicate for r in results)
    assert sum(r.chunks_inserted for r in results) == 1000
    assert sum(1 for r in results for s in r.signatures if s.is_new) == 1
    assert await count(engine, "chunks") == 1000
    assert await count(engine, "ingest_batches") == 50
    assert await count(engine, "signatures") == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 1000
    assert await fetch_value(engine, "SELECT committed_seq FROM streams") == 50
    assert (
        await fetch_value(engine, "SELECT CAST(sum(count) AS bigint) FROM signature_buckets")
        == 1000
    )


async def test_same_batch_sent_20_times_at_once_is_applied_once(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)
    chunks = make_chunks(
        "2024-01-01 10:00:00 ERROR payment 1 failed\n"
        "2024-01-01 10:00:10 ERROR payment 2 failed\n"
        "2024-01-01 10:00:20 INFO ok\n"
    )
    results = await asyncio.gather(*(commit(engine, sid, "same-key", 1, chunks) for _ in range(20)))

    assert sum(1 for r in results if not r.duplicate) == 1
    assert sum(1 for r in results if r.duplicate) == 19
    assert await count(engine, "ingest_batches") == 1
    assert await count(engine, "chunks") == 3
    assert await fetch_value(engine, "SELECT count FROM signatures") == 2


async def test_10000_identical_failures_make_one_signature(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)

    def raw_for(batch: int) -> str:
        lines = []
        for j in range(1000):
            i = batch * 1000 + j
            h, m, s = 10 + i // 3600, (i % 3600) // 60, i % 60
            lines.append(f"2024-01-01 {h:02d}:{m:02d}:{s:02d} ERROR payment {i} failed")
        return "\n".join(lines) + "\n"

    batches = [make_chunks(raw_for(b)) for b in range(10)]
    results = await asyncio.gather(
        *(commit(engine, sid, f"k{b}", b + 1, batches[b]) for b in range(10))
    )

    assert sum(1 for r in results for s in r.signatures if s.is_new) == 1
    assert await count(engine, "signatures") == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 10_000
    assert await count(engine, "chunks") == 10_000
    assert (
        await fetch_value(engine, "SELECT CAST(sum(count) AS bigint) FROM signature_buckets")
        == 10_000
    )
