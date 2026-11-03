"""Moves batches from the disk spool to the server and removes them only once committed.

step() does one unit of work and returns how many seconds the caller should wait before
calling it again (0.0 means "call again right away"). It never sleeps itself, which keeps it
easy to test; the CLI loop does the sleeping.

Delivery rules:
- Batches are sent in sequence order. A 2xx only means "queued on the server", so the batch
  stays in the spool until the server's committed_seq covers it.
- A batch that was accepted but not confirmed within resend_after_s is sent again with the
  same idempotency key (covers a server crash after the 202; the server drops duplicates).
- Retryable failures stop the step and return a backoff delay. Retry-After is honoured.
- A batch the server rejects outright (400/413/422 ...) is dropped from the spool and
  recorded in `dropped`, otherwise it would block every batch behind it forever.

Assumes the server commits the batches of one stream in sequence order, so that
committed_seq >= N means batches 1..N are all stored.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from shipper.backoff import Backoff
from shipper.reader import Batch
from shipper.sender import SendResult
from shipper.spool import Spool


class Sender(Protocol):
    async def send(self, batch: Batch) -> SendResult: ...

    async def committed_seq(self) -> int | None: ...


class Forwarder:
    def __init__(
        self,
        spool: Spool,
        client: Sender,
        backoff: Backoff,
        *,
        resend_after_s: float = 30.0,
        poll_s: float = 0.2,
        idle_s: float = 0.5,
        max_sends_per_step: int = 50,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if resend_after_s <= 0 or max_sends_per_step <= 0 or poll_s < 0 or idle_s < 0:
            raise ValueError("invalid forwarder settings")
        self._spool = spool
        self._client = client
        self._backoff = backoff
        self._resend_after_s = resend_after_s
        self._poll_s = poll_s
        self._idle_s = idle_s
        self._max_sends = max_sends_per_step
        self._clock = clock
        self._sent: dict[int, float] = {}
        self._attempt = 0
        self.committed = 0
        self.dropped: list[tuple[int, str]] = []

    async def step(self) -> float:
        remote = await self._client.committed_seq()
        if remote is not None:
            self.committed = max(self.committed, remote)
            self._spool.prune_through(self.committed)
        live = set(self._spool.pending_seqs())
        self._sent = {s: t for s, t in self._sent.items() if s in live}

        now = self._clock()
        sends = 0
        for seq in self._spool.pending_seqs():
            sent_at = self._sent.get(seq)
            if sent_at is not None and now - sent_at < self._resend_after_s:
                continue  # accepted recently, waiting for the commit
            if sends >= self._max_sends:
                return 0.0
            batch = self._spool.get(seq)
            if batch is None:
                continue
            sends += 1
            result = await self._client.send(batch)
            if result.kind == "retry":
                delay = self._backoff.delay(self._attempt, result.retry_after)
                self._attempt += 1
                return delay
            self._attempt = 0
            if result.kind == "accepted":
                self._sent[seq] = now
            else:
                self._spool.pop(seq)
                self.dropped.append((seq, result.detail))
        return self._poll_s if len(self._spool) else self._idle_s