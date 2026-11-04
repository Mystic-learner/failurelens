"""Event hub: one DB poller fans out events table rows to bounded per-client queues.

events.id comes from a sequence, so id 12 can commit before id 11. The poller therefore
does not use a plain "max id seen" cursor. It reads `id > low`, skipping ids it has already
sent, and delivers rows immediately, so a late commit of a lower id is still delivered once.
`low` only advances to the highest id whose created_at is older than lag_s, and the sent-set
is pruned below it, so memory stays bounded.

Known limit: a row whose transaction stays open longer than lag_s before committing can be
missed by live delivery (replay on reconnect with last_event_id still returns it).

Slow clients never block anyone: fan-out is put_nowait on a bounded queue per client. A full
queue drops that client (its backlog is discarded and a sentinel tells the writer to close
with 1013); everyone else is unaffected.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_SQL_POLL = text(
    """
    SELECT id, type, CAST(payload AS text), created_at,
           created_at < now() - make_interval(secs => CAST(:lag AS double precision)) AS settled
      FROM events
     WHERE id > CAST(:low AS bigint) AND NOT (id = ANY(CAST(:sent AS bigint[])))
     ORDER BY id
     LIMIT CAST(:n AS integer)
    """
)
_SQL_REPLAY = text(
    "SELECT id, type, CAST(payload AS text), created_at FROM events "
    "WHERE id > CAST(:last AS bigint) ORDER BY id LIMIT CAST(:n AS integer)"
)
_SQL_MAX = text("SELECT COALESCE(max(id), 0) FROM events")


class HubFull(Exception):
    """Raised when the maximum number of clients is already connected."""


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    payload: Any
    created_at: datetime

    def frame(self) -> str:
        return json.dumps(
            {
                "kind": "event",
                "id": self.id,
                "type": self.type,
                "payload": self.payload,
                "created_at": self.created_at.isoformat(),
            }
        )


def _event(row: Any) -> Event:
    return Event(int(row[0]), str(row[1]), json.loads(row[2]), row[3])


class Client:
    def __init__(self, maxsize: int) -> None:
        self.queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=maxsize)
        self.dropped = False


class EventHub:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        poll_s: float = 0.5,
        lag_s: float = 5.0,
        max_clients: int = 200,
        queue_size: int = 100,
        batch: int = 500,
        max_replay: int = 1000,
        send_timeout_s: float = 5.0,
    ) -> None:
        if poll_s <= 0 or lag_s < 0 or send_timeout_s <= 0:
            raise ValueError("poll_s and send_timeout_s must be > 0, lag_s >= 0")
        if max_clients < 1 or queue_size < 1 or batch < 1 or max_replay < 1:
            raise ValueError("max_clients, queue_size, batch and max_replay must be >= 1")
        self._engine = engine
        self._poll_s = poll_s
        self._lag_s = lag_s
        self._max_clients = max_clients
        self._queue_size = queue_size
        self._batch = batch
        self._max_replay = max_replay
        self.send_timeout_s = send_timeout_s
        self._clients: set[Client] = set()
        self._sent: set[int] = set()
        self._low = 0
        self._ready = False
        self._dropped = 0
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def stats(self) -> dict[str, int]:
        return {
            "low": self._low,
            "tracked": len(self._sent),
            "clients": len(self._clients),
            "dropped": self._dropped,
        }

    def subscribe(self) -> Client:
        if len(self._clients) >= self._max_clients:
            raise HubFull
        client = Client(self._queue_size)
        self._clients.add(client)
        return client

    def unsubscribe(self, client: Client) -> None:
        self._clients.discard(client)

    def _close_client(self, client: Client) -> None:
        client.dropped = True
        self._clients.discard(client)
        while not client.queue.empty():
            client.queue.get_nowait()
        client.queue.put_nowait(None)

    def _publish(self, event: Event) -> None:
        for client in list(self._clients):
            try:
                client.queue.put_nowait(event)
            except asyncio.QueueFull:
                self._dropped += 1
                self._close_client(client)

    async def poll_once(self) -> int:
        """Fetch and fan out everything not yet delivered. Returns the number of events."""
        if not self._ready:
            await self._init_low()
            return 0
        delivered = 0
        while True:
            async with self._engine.connect() as conn:
                rows = (
                    await conn.execute(
                        _SQL_POLL,
                        {
                            "lag": self._lag_s,
                            "low": self._low,
                            "sent": sorted(self._sent),
                            "n": self._batch,
                        },
                    )
                ).all()
            new_low = self._low
            for row in rows:
                event = _event(row)
                self._sent.add(event.id)
                self._publish(event)
                delivered += 1
                if row[4] and event.id > new_low:
                    new_low = event.id
            if new_low > self._low:
                self._low = new_low
                self._sent = {i for i in self._sent if i > self._low}
            if len(rows) < self._batch:
                return delivered

    async def replay(self, last_id: int) -> tuple[list[Event], bool]:
        """Events with id > last_id, at most max_replay. Second value: more were left out."""
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(_SQL_REPLAY, {"last": last_id, "n": self._max_replay + 1})
            ).all()
        events = [_event(r) for r in rows]
        return events[: self._max_replay], len(events) > self._max_replay

    async def start(self) -> None:
        if self._task is not None:
            return
        try:
            await self._init_low()
        except Exception:
            # The app must boot with the database down (health reports 503). The poller
            # retries the initialisation on every cycle until the database answers.
            logger.warning("event hub could not read max(id) at start; will retry")
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def _init_low(self) -> None:
        async with self._engine.connect() as conn:
            self._low = int((await conn.execute(_SQL_MAX)).scalar_one())
        self._ready = True

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
        for client in list(self._clients):
            self._close_client(client)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception:
                logger.exception("event hub poll failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_s)
            except TimeoutError:
                pass
