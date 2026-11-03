import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

# Each task opens its own connection, but only 20 at once so we stay under max_connections.
PARALLELISM = 20

INSERT_CHUNK = (
    "INSERT INTO chunks (text, chunk_hash) VALUES ('same error', 'one-hash') "
    "ON CONFLICT (chunk_hash) DO NOTHING"
)

UPSERT_SIGNATURE = """
    INSERT INTO signatures (service, fingerprint, level, count, first_seen, last_seen)
    VALUES (:service, :fp, 'ERROR', 1, now(), now())
    ON CONFLICT (service, fingerprint)
    DO UPDATE SET count = signatures.count + 1, last_seen = now()
"""

UPSERT_BUCKET = """
    INSERT INTO signature_buckets (signature_id, minute, count)
    VALUES (:sid, date_trunc('minute', now()), 1)
    ON CONFLICT (signature_id, minute)
    DO UPDATE SET count = signature_buckets.count + 1
"""


async def run_parallel(n: int, job: Callable[[int], Awaitable[Any]]) -> list[Any]:
    gate = asyncio.Semaphore(PARALLELISM)

    async def guarded(i: int) -> Any:
        async with gate:
            return await job(i)

    return await asyncio.gather(*(guarded(i) for i in range(n)))


async def test_50_parallel_inserts_of_the_same_chunk_leave_one_row(engine: AsyncEngine) -> None:
    async def insert(_: int) -> int:
        async with engine.begin() as conn:
            return (await conn.execute(text(INSERT_CHUNK))).rowcount

    inserted = await run_parallel(50, insert)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT count(*) FROM chunks"))).scalar_one()
    assert rows == 1
    assert sum(inserted) == 1  # exactly one writer won


async def test_100_parallel_upserts_give_an_exact_signature_count(engine: AsyncEngine) -> None:
    async def upsert(_: int) -> None:
        async with engine.begin() as conn:
            await conn.execute(text(UPSERT_SIGNATURE), {"service": "api", "fp": "abc"})

    await run_parallel(100, upsert)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT count, first_seen <= last_seen FROM signatures"))).all()
    assert len(rows) == 1
    assert tuple(rows[0]) == (100, True)


async def test_parallel_bucket_upserts_do_not_lose_counts(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        sid = (
            await conn.execute(
                text("INSERT INTO signatures (service, fingerprint) VALUES ('api', 'b') RETURNING id")
            )
        ).scalar_one()

    async def upsert(_: int) -> None:
        async with engine.begin() as conn:
            await conn.execute(text(UPSERT_BUCKET), {"sid": sid})

    await run_parallel(60, upsert)
    async with engine.connect() as conn:
        total = (await conn.execute(text("SELECT sum(count) FROM signature_buckets"))).scalar_one()
    assert total == 60


async def test_same_fingerprint_in_two_services_is_two_signatures(engine: AsyncEngine) -> None:
    async def upsert(i: int) -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text(UPSERT_SIGNATURE), {"service": "api" if i % 2 else "worker", "fp": "same"}
            )

    await run_parallel(10, upsert)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(text("SELECT service, count FROM signatures ORDER BY service"))
        ).all()
    assert [tuple(r) for r in rows] == [("api", 5), ("worker", 5)]
