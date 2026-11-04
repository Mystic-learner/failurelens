"""GET /ws/events (WebSocket). Optional ?last_event_id=N (or Last-Event-ID header) replays
missed events first. Frames are JSON with a "kind": event | ready | replay_truncated.
Clients must dedupe by event id. Close codes: 1008 bad cursor, 1013 overloaded/slow consumer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Protocol

from fastapi import APIRouter, WebSocket

from app.services.event_hub import Event, EventHub, HubFull

logger = logging.getLogger(__name__)
router = APIRouter()


class WebSocketLike(Protocol):
    async def accept(self) -> None: ...
    async def send_text(self, data: str) -> None: ...
    async def receive_text(self) -> str: ...
    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


async def _until_disconnect(ws: WebSocketLike) -> None:
    """Swallow client messages; return when the connection is gone."""
    try:
        while True:
            await ws.receive_text()
    except Exception:
        return


async def _send(ws: WebSocketLike, data: str, seconds: float) -> None:
    await asyncio.wait_for(ws.send_text(data), seconds)


async def _close(ws: WebSocketLike, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(ws.close(code=code, reason=reason), 2.0)


async def serve_client(
    ws: WebSocketLike,
    hub: EventHub,
    last_event_id: int | None,
    send_timeout: float | None = None,
) -> None:
    timeout = send_timeout if send_timeout is not None else hub.send_timeout_s
    await ws.accept()
    try:
        client = hub.subscribe()
    except HubFull:
        await _close(ws, 1013, "too many clients")
        return
    seen: set[int] = set()
    reader = asyncio.create_task(_until_disconnect(ws))
    getter: asyncio.Future[Event | None] | None = None
    try:
        # Subscribed before replaying, so nothing falls between replay and live delivery.
        # Events present in both are skipped through `seen`.
        if last_event_id is not None:
            events, truncated = await hub.replay(last_event_id)
            for event in events:
                await _send(ws, event.frame(), timeout)
                seen.add(event.id)
            if truncated:
                frame = {"kind": "replay_truncated", "last_id": events[-1].id}
                await _send(ws, json.dumps(frame), timeout)
        await _send(ws, json.dumps({"kind": "ready"}), timeout)
        while True:
            getter = asyncio.ensure_future(client.queue.get())
            done, _ = await asyncio.wait({getter, reader}, return_when=asyncio.FIRST_COMPLETED)
            if reader in done:
                return
            item = getter.result()
            if item is None:
                await _close(ws, 1013, "slow consumer: reconnect with last_event_id")
                return
            if item.id in seen:
                continue
            await _send(ws, item.frame(), timeout)
    except Exception as exc:
        logger.info("websocket client dropped: %s", type(exc).__name__)
        await _close(ws, 1013, "send failed")
    finally:
        if getter is not None and not getter.done():
            getter.cancel()
        reader.cancel()
        hub.unsubscribe(client)


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    raw = websocket.query_params.get("last_event_id") or websocket.headers.get("last-event-id")
    last: int | None = None
    if raw is not None:
        if not raw.isdigit() or len(raw) > 18:
            await websocket.close(code=1008, reason="invalid last_event_id")
            return
        last = int(raw)
    hub: EventHub | None = getattr(websocket.app.state, "event_hub", None)
    if hub is None:
        await websocket.close(code=1013, reason="events unavailable")
        return
    await serve_client(websocket, hub, last)
