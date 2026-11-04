from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import ws as ws_module
from app.api.ws import serve_client
from app.core.middleware import BodyLimitMiddleware
from app.core.request_id import RequestIdMiddleware
from app.services.event_hub import Client, EventHub, HubFull

pytestmark = pytest.mark.asyncio


async def add_event(engine: AsyncEngine, id_: int, age_s: float = 0.0) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO events (id, type, payload, created_at) VALUES "
                "(CAST(:id AS bigint), 'test.event', CAST(CAST(:p AS text) AS jsonb), "
                "now() - make_interval(secs => CAST(:age AS double precision)))"
            ),
            {"id": id_, "p": json.dumps({"n": id_}), "age": age_s},
        )


async def primed(engine: AsyncEngine, **kwargs: Any) -> EventHub:
    """A hub that finished its first-poll initialisation on an empty table (low=0)."""
    hub = EventHub(engine, **kwargs)
    assert await hub.poll_once() == 0
    return hub


def drain(client: Client) -> list[int | None]:
    items: list[int | None] = []
    while not client.queue.empty():
        event = client.queue.get_nowait()
        items.append(None if event is None else event.id)
    return items


async def until(predicate: Callable[[], bool], within: float = 3.0) -> None:
    async with asyncio.timeout(within):
        while not predicate():  # noqa: ASYNC110 - polling a plain predicate, no event exists
            await asyncio.sleep(0.01)


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int, str | None] | None = None
        self.gate = asyncio.Event()
        self.gate.set()
        self.blocked = False
        self.delay = 0.0
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        if not self.gate.is_set():
            self.blocked = True
        await self.gate.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        self.sent.append(json.loads(data))

    async def receive_text(self) -> str:
        item = await self._incoming.get()
        if item is None:
            raise RuntimeError("disconnected")
        return item

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason)

    def disconnect(self) -> None:
        self._incoming.put_nowait(None)

    def event_ids(self) -> list[int]:
        return [f["id"] for f in self.sent if f["kind"] == "event"]

    def ready(self) -> bool:
        return any(f["kind"] == "ready" for f in self.sent)


async def test_late_committing_lower_id_is_still_delivered_exactly_once(
    engine: AsyncEngine,
) -> None:
    hub = await primed(engine, lag_s=5.0)
    client = hub.subscribe()
    await add_event(engine, 12)
    await hub.poll_once()
    assert drain(client) == [12]
    await add_event(engine, 11)  # committed after 12: a plain cursor would lose it
    await hub.poll_once()
    assert drain(client) == [11]
    await hub.poll_once()
    assert drain(client) == []


async def test_low_water_mark_advances_and_sent_set_is_pruned(engine: AsyncEngine) -> None:
    hub = await primed(engine, lag_s=5.0)
    client = hub.subscribe()
    await add_event(engine, 1)
    await add_event(engine, 2)
    await hub.poll_once()
    assert hub.stats()["low"] == 0 and hub.stats()["tracked"] == 2
    await add_event(engine, 20, age_s=10.0)  # older than the lag window
    await hub.poll_once()
    assert hub.stats()["low"] == 20 and hub.stats()["tracked"] == 0
    assert drain(client) == [1, 2, 20]
    await add_event(engine, 21)
    await hub.poll_once()
    assert drain(client) == [21]


async def test_replay_returns_events_after_the_cursor(engine: AsyncEngine) -> None:
    hub = EventHub(engine)
    for i in range(1, 6):
        await add_event(engine, i)
    events, truncated = await hub.replay(2)
    assert [e.id for e in events] == [3, 4, 5] and truncated is False
    assert events[0].payload == {"n": 3}


async def test_replay_is_capped_and_flags_truncation(engine: AsyncEngine) -> None:
    hub = EventHub(engine, max_replay=3)
    for i in range(1, 6):
        await add_event(engine, i)
    events, truncated = await hub.replay(0)
    assert [e.id for e in events] == [1, 2, 3] and truncated is True


async def test_slow_client_is_dropped_and_others_are_unaffected(engine: AsyncEngine) -> None:
    hub = await primed(engine, queue_size=2)
    slow = hub.subscribe()
    fast = hub.subscribe()
    got: list[int | None] = []
    for i in range(1, 6):
        await add_event(engine, i)
        await asyncio.wait_for(hub.poll_once(), 2.0)
        got += drain(fast)
    assert got == [1, 2, 3, 4, 5]
    assert drain(slow) == [None]  # backlog discarded, sentinel left
    assert slow.dropped is True and fast.dropped is False
    assert hub.stats()["clients"] == 1 and hub.stats()["dropped"] == 1


async def test_client_cap(engine: AsyncEngine) -> None:
    hub = EventHub(engine, max_clients=2)
    a = hub.subscribe()
    hub.subscribe()
    with pytest.raises(HubFull):
        hub.subscribe()
    hub.unsubscribe(a)
    hub.subscribe()


async def test_invalid_hub_config_is_rejected(engine: AsyncEngine) -> None:
    for kwargs in ({"poll_s": 0}, {"lag_s": -1}, {"max_clients": 0}, {"queue_size": 0}):
        with pytest.raises(ValueError):
            EventHub(engine, **kwargs)  # type: ignore[arg-type]


async def test_started_hub_delivers_only_new_events(engine: AsyncEngine) -> None:
    await add_event(engine, 1)
    hub = EventHub(engine, poll_s=0.05)
    await hub.start()
    try:
        client = hub.subscribe()
        await add_event(engine, 2)
        await until(lambda: not client.queue.empty())
        assert drain(client) == [2]
    finally:
        await hub.stop()


async def test_stop_closes_connected_clients(engine: AsyncEngine) -> None:
    hub = EventHub(engine, poll_s=0.05)
    await hub.start()
    client = hub.subscribe()
    await hub.stop()
    assert drain(client) == [None]


async def test_serve_client_replays_then_goes_live_without_duplicates(
    engine: AsyncEngine,
) -> None:
    for i in (1, 2, 3):
        await add_event(engine, i)
    hub = EventHub(engine, poll_s=0.05)
    await hub.start()  # like the app: start() sets the low-water mark to the current max id
    ws = FakeWebSocket()
    task = asyncio.create_task(serve_client(ws, hub, 1))
    await until(ws.ready)
    assert ws.accepted and ws.event_ids() == [2, 3]
    await add_event(engine, 4)
    await until(lambda: 4 in ws.event_ids())
    assert ws.event_ids() == [2, 3, 4]
    ws.disconnect()
    await asyncio.wait_for(task, 3.0)
    assert hub.stats()["clients"] == 0
    await hub.stop()


async def test_serve_client_flags_truncated_replay(engine: AsyncEngine) -> None:
    for i in range(1, 6):
        await add_event(engine, i)
    hub = EventHub(engine, max_replay=2)
    ws = FakeWebSocket()
    task = asyncio.create_task(serve_client(ws, hub, 0))
    await until(ws.ready)
    kinds = [f["kind"] for f in ws.sent]
    assert kinds == ["event", "event", "replay_truncated", "ready"]
    assert ws.sent[2]["last_id"] == 2
    ws.disconnect()
    await asyncio.wait_for(task, 3.0)


async def test_serve_client_rejects_when_hub_is_full(engine: AsyncEngine) -> None:
    hub = EventHub(engine, max_clients=1)
    hub.subscribe()
    ws = FakeWebSocket()
    await serve_client(ws, hub, None)
    assert ws.closed is not None and ws.closed[0] == 1013


async def test_serve_client_closes_a_consumer_whose_sends_time_out(engine: AsyncEngine) -> None:
    hub = await primed(engine)
    ws = FakeWebSocket()
    task = asyncio.create_task(serve_client(ws, hub, None, send_timeout=0.05))
    await until(ws.ready)
    ws.delay = 1.0
    await add_event(engine, 1)
    await hub.poll_once()
    await asyncio.wait_for(task, 3.0)
    assert ws.closed is not None and ws.closed[0] == 1013
    assert hub.stats()["clients"] == 0


async def test_serve_client_closes_with_1013_when_its_queue_overflows(
    engine: AsyncEngine,
) -> None:
    hub = await primed(engine, queue_size=2)
    ws = FakeWebSocket()
    task = asyncio.create_task(serve_client(ws, hub, None, send_timeout=10.0))
    await until(ws.ready)
    ws.gate.clear()  # the client stops reading
    await add_event(engine, 1)
    await hub.poll_once()
    await until(lambda: ws.blocked)  # writer is stuck sending event 1
    for i in (2, 3, 4):  # 2 and 3 fill the queue, 4 overflows it
        await add_event(engine, i)
        await hub.poll_once()
    assert hub.stats()["dropped"] == 1
    ws.gate.set()
    await asyncio.wait_for(task, 3.0)
    assert ws.closed is not None and ws.closed[0] == 1013
    assert ws.event_ids() == [1]  # discarded backlog; client reconnects with last_event_id


def _app(hub: EventHub) -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware, max_bytes=1024)
    app.add_middleware(RequestIdMiddleware)
    app.state.event_hub = hub
    app.include_router(ws_module.router)
    return app


async def test_ws_route_works_behind_the_real_middleware(engine: AsyncEngine) -> None:
    for i in (1, 2, 3):
        await add_event(engine, i)
    app = _app(EventHub(engine))
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws/events?last_event_id=1") as sock:
            frames = [sock.receive_json() for _ in range(3)]
        assert [f["kind"] for f in frames] == ["event", "event", "ready"]
        assert [f.get("id") for f in frames[:2]] == [2, 3]
        with tc.websocket_connect("/ws/events", headers={"Last-Event-ID": "2"}) as sock:
            assert sock.receive_json()["id"] == 3


async def test_ws_route_rejects_a_bad_cursor(engine: AsyncEngine) -> None:
    app = _app(EventHub(engine))
    with TestClient(app) as tc:
        for bad in ("abc", "-1", "9" * 30):
            with pytest.raises(WebSocketDisconnect):
                with tc.websocket_connect(f"/ws/events?last_event_id={bad}"):
                    pass


async def test_ws_route_without_a_hub_closes_cleanly() -> None:
    app = FastAPI()
    app.include_router(ws_module.router)
    with TestClient(app) as tc:
        with pytest.raises(WebSocketDisconnect):
            with tc.websocket_connect("/ws/events"):
                pass


async def test_first_poll_initialises_and_does_not_replay_history(engine: AsyncEngine) -> None:
    await add_event(engine, 1)
    hub = EventHub(engine)
    client = hub.subscribe()
    assert await hub.poll_once() == 0
    assert drain(client) == []
    await add_event(engine, 2)
    await hub.poll_once()
    assert drain(client) == [2]


async def test_start_survives_an_unreachable_database() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    dead = create_async_engine(
        "postgresql+asyncpg://x:y@127.0.0.1:1/z",
        poolclass=NullPool,
        connect_args={"timeout": 1},
    )
    hub = EventHub(dead, poll_s=0.05)
    try:
        await hub.start()  # must not raise: the app has to boot with the DB down
        await asyncio.sleep(0.15)
        assert hub.stats()["low"] == 0
    finally:
        await hub.stop()
        await dead.dispose()
