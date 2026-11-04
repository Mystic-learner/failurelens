"""Bounded in-memory queue between the ingest endpoint and the workers.

Memory is bounded two ways: by item count and by total queued bytes. put() never
blocks and never grows past either limit: it raises QueueFull, and the endpoint turns
that into 429 with Retry-After (backpressure). A single item larger than max_bytes is
accepted only when the queue is empty, so one big batch cannot be rejected forever.

Shutdown: close() stops accepting (put raises QueueClosed). Workers keep calling get()
until it returns None (closed AND empty). drain() = close() + wait until every item
that was put has been marked with task_done(), so nothing accepted is lost.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field


class QueueFull(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__("ingest queue is full")
        self.retry_after_s = retry_after_s


class QueueClosed(Exception):
    """The queue is shutting down and accepts nothing new."""


@dataclass(frozen=True)
class IngestItem:
    service: str
    source_id: str
    idempotency_key: str
    seq: int
    data: bytes
    received_at: float = field(default_factory=time.monotonic)

    @property
    def size(self) -> int:
        return len(self.data)


class IngestQueue:
    def __init__(self, max_items: int, max_bytes: int, retry_after_s: int = 1) -> None:
        if max_items <= 0 or max_bytes <= 0:
            raise ValueError("max_items and max_bytes must be > 0")
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._retry_after_s = retry_after_s
        self._items: deque[IngestItem] = deque()
        self._bytes = 0
        self._closed = False
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._unfinished = 0
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def depth(self) -> int:
        return len(self._items)

    @property
    def queued_bytes(self) -> int:
        return self._bytes

    @property
    def max_items(self) -> int:
        return self._max_items

    @property
    def closed(self) -> bool:
        return self._closed

    def put(self, item: IngestItem) -> None:
        if self._closed:
            raise QueueClosed()
        if len(self._items) >= self._max_items:
            raise QueueFull(self._retry_after_s)
        if self._items and self._bytes + item.size > self._max_bytes:
            raise QueueFull(self._retry_after_s)
        self._items.append(item)
        self._bytes += item.size
        self._unfinished += 1
        self._idle.clear()
        self._wake_one()

    async def get(self) -> IngestItem | None:
        """Next item, or None once the queue is closed and empty."""
        while True:
            if self._items:
                item = self._items.popleft()
                self._bytes -= item.size
                return item
            if self._closed:
                return None
            waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            try:
                await waiter
            except asyncio.CancelledError:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                elif waiter.done() and not waiter.cancelled():
                    self._wake_one()  # we were woken but are being cancelled: pass it on
                raise

    def task_done(self) -> None:
        if self._unfinished <= 0:
            raise ValueError("task_done() called more times than items were put")
        self._unfinished -= 1
        if self._unfinished == 0:
            self._idle.set()

    def close(self) -> None:
        self._closed = True
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)

    async def drain(self) -> None:
        """Stop accepting, then wait until every accepted item has been processed."""
        self.close()
        await self._idle.wait()

    def _wake_one(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                return
