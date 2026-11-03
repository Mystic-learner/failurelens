from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.storage import commit_batch, get_or_create_stream
from tests.storagetools import (
    NOW,
    commit,
    count,
    fetch_all,
    fetch_value,
    make_chunks,
    setup_stream,
)

pytestmark = pytest.mark.asyncio

ERRORS = (
    "2024-01-01 10:00:00 ERROR payment 1 failed for user 11\n"
    "2024-01-01 10:00:10 ERROR payment 22 failed for user 12\n"
    "2024-01-01 10:00:20 INFO checkout ok\n"
)


async def test_get_or_create_stream_is_idempotent(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        a = await get_or_create_stream(conn, "svc", "src")
        b = await get_or_create_stream(conn, "svc", "src")
        c = await get_or_create_stream(conn, "other", "src")
    assert a == b
    assert c != a
    assert await count(engine, "streams") == 2


async def test_commit_stores_chunks_signature_and_batch(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)
    chunks = make_chunks(ERRORS)
    assert len(chunks) == 3
    result = await commit(engine, sid, "k1", 7, chunks)

    assert result.duplicate is False
    assert result.chunks_received == 3
    assert result.chunks_inserted == 3
    assert len(result.signatures) == 1
    hit = result.signatures[0]
    assert hit.is_new is True
    assert hit.batch_count == 2
    assert hit.total_count == 2

    assert await count(engine, "chunks") == 3
    assert await count(engine, "ingest_batches") == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 2
    assert await fetch_value(engine, "SELECT committed_seq FROM streams") == 7


async def test_chunk_columns_are_stored(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)
    chunks = make_chunks("2024-01-01 10:00:00 ERROR connect ECONNRESET to upstream\n")
    await commit(engine, sid, "k1", 1, chunks)

    row = (
        await fetch_all(
            engine, "SELECT level, service, fingerprint, ts, error_codes, stream_id FROM chunks"
        )
    )[0]
    assert row.level == "ERROR"
    assert row.service == "svc"
    assert row.fingerprint == chunks[0].fingerprint
    assert row.ts == datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
    assert "ECONNRESET" in row.error_codes
    assert row.stream_id == sid


async def test_duplicate_batch_is_ignored(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)
    chunks = make_chunks(ERRORS)
    await commit(engine, sid, "k1", 1, chunks)
    second = await commit(engine, sid, "k1", 1, chunks)

    assert second.duplicate is True
    assert second.chunks_inserted == 0
    assert second.signatures == ()
    assert await count(engine, "chunks") == 3
    assert await count(engine, "ingest_batches") == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 2


async def test_same_chunks_under_a_new_key_are_counted_but_not_stored_twice(
    engine: AsyncEngine,
) -> None:
    sid = await setup_stream(engine)
    chunks = make_chunks(ERRORS)
    await commit(engine, sid, "k1", 1, chunks)
    second = await commit(engine, sid, "k2", 2, chunks)

    assert second.duplicate is False
    assert second.chunks_inserted == 0
    assert second.signatures[0].is_new is False
    assert second.signatures[0].total_count == 4
    assert await count(engine, "chunks") == 3
    assert await count(engine, "ingest_batches") == 2


async def test_identical_text_errors_are_all_counted_but_stored_once(
    engine: AsyncEngine,
) -> None:
    sid = await setup_stream(engine)
    chunks = make_chunks("ERROR boom\n" * 1000)
    assert len(chunks) == 1000
    result = await commit(engine, sid, "k1", 1, chunks)

    assert result.chunks_received == 1000
    assert result.chunks_inserted == 1
    assert await count(engine, "chunks") == 1
    assert await count(engine, "signatures") == 1
    assert await fetch_value(engine, "SELECT count FROM signatures") == 1000


async def test_services_have_separate_signatures(engine: AsyncEngine) -> None:
    a = await setup_stream(engine, "a", "src-a")
    b = await setup_stream(engine, "b", "src-b")
    await commit(engine, a, "k1", 1, make_chunks(ERRORS, "src-a"), service="a")
    await commit(engine, b, "k1", 1, make_chunks(ERRORS, "src-b"), service="b")

    rows = await fetch_all(engine, "SELECT service, count FROM signatures ORDER BY service")
    assert [(r.service, r.count) for r in rows] == [("a", 2), ("b", 2)]
    assert await count(engine, "chunks") == 6


async def test_signature_keeps_first_exemplar_and_widens_time_range(
    engine: AsyncEngine,
) -> None:
    sid = await setup_stream(engine)
    await commit(
        engine, sid, "k1", 1, make_chunks("2024-01-01 10:00:00 ERROR payment 1 failed\n")
    )
    second = await commit(
        engine, sid, "k2", 2, make_chunks("2024-01-01 10:05:00 ERROR payment 2 failed\n")
    )
    assert second.signatures[0].is_new is False
    assert second.signatures[0].batch_count == 1
    assert second.signatures[0].total_count == 2

    row = (
        await fetch_all(
            engine,
            "SELECT s.first_seen, s.last_seen, s.count, c.text AS exemplar_text "
            "FROM signatures s JOIN chunks c ON c.id = s.exemplar_chunk_id",
        )
    )[0]
    assert row.first_seen == datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
    assert row.last_seen == datetime(2024, 1, 1, 10, 5, 0, tzinfo=UTC)
    assert row.count == 2
    assert row.exemplar_text.startswith("2024-01-01 10:00:00")


async def test_minute_buckets(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)
    raw = (
        "2024-01-01 10:00:10 ERROR payment 1 failed\n"
        "2024-01-01 10:00:50 ERROR payment 2 failed\n"
        "2024-01-01 10:01:05 ERROR payment 3 failed\n"
    )
    await commit(engine, sid, "k1", 1, make_chunks(raw))
    rows = await fetch_all(engine, "SELECT minute, count FROM signature_buckets ORDER BY minute")
    assert [(r.minute, r.count) for r in rows] == [
        (datetime(2024, 1, 1, 10, 0, tzinfo=UTC), 2),
        (datetime(2024, 1, 1, 10, 1, tzinfo=UTC), 1),
    ]


@pytest.mark.parametrize("raw", ["2031-01-01 10:00:00 ERROR boom 5\n", "ERROR boom 5\n"])
async def test_untrusted_timestamps_bucket_into_now(engine: AsyncEngine, raw: str) -> None:
    sid = await setup_stream(engine)
    chunks = make_chunks(raw)
    assert chunks[0].fingerprint is not None
    await commit(engine, sid, "k1", 1, chunks)
    rows = await fetch_all(engine, "SELECT minute, count FROM signature_buckets")
    assert [(r.minute, r.count) for r in rows] == [(datetime(2030, 1, 1, 12, 0, tzinfo=UTC), 1)]


async def test_empty_batch_is_recorded(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)
    result = await commit(engine, sid, "k1", 5, [])
    assert result.duplicate is False
    assert result.signatures == ()
    assert await count(engine, "ingest_batches") == 1
    assert await count(engine, "chunks") == 0
    assert await fetch_value(engine, "SELECT committed_seq FROM streams") == 5


async def test_failed_commit_rolls_back_and_replay_succeeds(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)
    chunks = make_chunks(ERRORS)

    with pytest.raises(RuntimeError):
        async with engine.begin() as conn:
            await commit_batch(
                conn,
                stream_id=sid,
                service="svc",
                idempotency_key="k1",
                seq=1,
                line_count=3,
                chunks=chunks,
                now=NOW,
            )
            raise RuntimeError("worker crashed before commit")

    for table in ("ingest_batches", "chunks", "signatures", "signature_buckets"):
        assert await count(engine, table) == 0

    replay = await commit(engine, sid, "k1", 1, chunks)
    assert replay.duplicate is False
    assert await count(engine, "chunks") == 3
    assert await fetch_value(engine, "SELECT count FROM signatures") == 2


async def test_negative_seq_is_rejected(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)
    with pytest.raises(ValueError):
        await commit(engine, sid, "k1", -1, [])