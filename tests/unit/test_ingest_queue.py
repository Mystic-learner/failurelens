import asyncio

import pytest

from app.services.ingest_queue import IngestItem, IngestQueue, QueueClosed, QueueFull

pytestmark = pytest.mark.asyncio


def item(seq: int, size: int = 10) -> IngestItem:
    return IngestItem("svc", "src", f"k{seq}", seq, b"x" * size)


async def test_fifo_and_depth() -> None:
    q = IngestQueue(max_items=5, max_bytes=1000)
    for i in range(3):
        q.put(item(i))
    assert q.depth == 3
    assert q.queued_bytes == 30
    got = [(await q.get()) for _ in range(3)]
    assert [g.seq for g in got if g is not None] == [0, 1, 2]
    assert q.depth == 0
    assert q.queued_bytes == 0


async def test_full_by_count_raises_with_retry_after() -> None:
    q = IngestQueue(max_items=2, max_bytes=1000, retry_after_s=3)
    q.put(item(1))
    q.put(item(2))
    with pytest.raises(QueueFull) as exc:
        q.put(item(3))
    assert exc.value.retry_after_s == 3
    assert q.depth == 2


async def test_full_by_bytes() -> None:
    q = IngestQueue(max_items=100, max_bytes=100)
    q.put(item(1, 60))
    with pytest.raises(QueueFull):
        q.put(item(2, 60))
    q.put(item(3, 40))
    assert q.queued_bytes == 100


async def test_oversized_item_is_accepted_only_into_an_empty_queue() -> None:
    q = IngestQueue(max_items=10, max_bytes=100)
    q.put(item(1, 500))
    with pytest.raises(QueueFull):
        q.put(item(2, 1))
    got = await q.get()
    assert got is not None
    assert got.seq == 1
    q.put(item(3, 500))


async def test_get_waits_for_an_item() -> None:
    q = IngestQueue(max_items=2, max_bytes=1000)
    getter = asyncio.create_task(q.get())
    await asyncio.sleep(0)
    assert not getter.done()
    q.put(item(7))
    got = await asyncio.wait_for(getter, 1)
    assert got is not None
    assert got.seq == 7


async def test_close_wakes_waiting_getters_with_none() -> None:
    q = IngestQueue(max_items=2, max_bytes=1000)
    getters = [asyncio.create_task(q.get()) for _ in range(3)]
    await asyncio.sleep(0)
    q.close()
    assert await asyncio.wait_for(asyncio.gather(*getters), 1) == [None, None, None]


async def test_closed_queue_still_hands_out_what_it_holds() -> None:
    q = IngestQueue(max_items=5, max_bytes=1000)
    q.put(item(1))
    q.put(item(2))
    q.close()
    with pytest.raises(QueueClosed):
        q.put(item(3))
    first = await q.get()
    second = await q.get()
    assert first is not None and second is not None
    assert [first.seq, second.seq] == [1, 2]
    assert await q.get() is None


async def test_drain_waits_until_every_item_is_done() -> None:
    q = IngestQueue(max_items=5, max_bytes=1000)
    q.put(item(1))
    q.put(item(2))
    drain = asyncio.create_task(q.drain())
    await asyncio.sleep(0)
    assert not drain.done()
    for _ in range(2):
        got = await q.get()
        assert got is not None
    await asyncio.sleep(0)
    assert not drain.done()  # taken but not finished: still in flight
    q.task_done()
    await asyncio.sleep(0)
    assert not drain.done()
    q.task_done()
    await asyncio.wait_for(drain, 1)


async def test_drain_on_an_empty_queue_returns_immediately() -> None:
    q = IngestQueue(max_items=5, max_bytes=1000)
    await asyncio.wait_for(q.drain(), 1)
    assert q.closed


async def test_task_done_too_many_times_raises() -> None:
    q = IngestQueue(max_items=5, max_bytes=1000)
    with pytest.raises(ValueError):
        q.task_done()


async def test_cancelled_getter_does_not_swallow_the_item() -> None:
    q = IngestQueue(max_items=5, max_bytes=1000)
    first = asyncio.create_task(q.get())
    second = asyncio.create_task(q.get())
    await asyncio.sleep(0)
    q.put(item(9))
    first.cancel()
    got = await asyncio.wait_for(second, 1)
    assert got is not None
    assert got.seq == 9
    with pytest.raises(asyncio.CancelledError):
        await first


async def test_invalid_limits_are_rejected() -> None:
    with pytest.raises(ValueError):
        IngestQueue(max_items=0, max_bytes=10)
    with pytest.raises(ValueError):
        IngestQueue(max_items=10, max_bytes=0)


async def test_flood_never_exceeds_the_bounds() -> None:
    q = IngestQueue(max_items=100, max_bytes=10_000)
    accepted = rejected = 0
    for i in range(10_000):
        try:
            q.put(item(i, 50))
            accepted += 1
        except QueueFull:
            rejected += 1
        assert q.depth <= 100
        assert q.queued_bytes <= 10_000
    assert accepted == 100
    assert rejected == 9_900