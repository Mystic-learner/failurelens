"""Log file reader: complete lines in, numbered batches out. No network here.

Delivery model (at least once):
- next_batch() returns the batch in flight until ack() is called for it. ack() is what moves
  the saved offset forward, so a crash before ack() makes the next run resend the same batch.
- When a batch is cut, its descriptor (file id, byte range, seq) is saved BEFORE it is
  returned. After a restart the same byte range is re-read and resent with the same key, so
  batch boundaries never drift and the server's duplicate check absorbs the replay.
- The idempotency key depends only on (source_id, file id, generation, start, end).

Files:
- Rotation is detected by inode. With hold_open (default on Linux/macOS) the old file stays
  open, so its unread tail, even an unterminated last line, is shipped before switching.
  Windows cannot rename a file that is held open, so hold_open defaults to False there and
  lines written between the last poll and a rotation can be lost.
- Truncation in place (size < position) restarts at offset 0 with generation + 1.
- Only complete lines are shipped; an unterminated last line waits for its newline.
- A line longer than max_line_bytes is split into pieces, nothing is dropped.

Known limits: if a rotated file's inode is reused by the new file and it has already grown
past our offset, the rotation is not noticed. If the file that held an unacknowledged batch
is gone after a restart, that batch is dropped (lost_inflight=True); its seq is not reused.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from shipper.state import Inflight, StateStore

logger = logging.getLogger("failurelens.shipper")


@dataclass(frozen=True)
class Batch:
    seq: int
    key: str  # idempotency key
    data: bytes  # raw bytes to send; always ends with a newline
    file_id: str
    gen: int
    start: int  # byte range [start, end) in the file
    end: int
    lines: int


def file_ident(st: os.stat_result) -> str:
    return f"{st.st_dev}:{st.st_ino}"


def make_key(source_id: str, file_id: str, gen: int, start: int, end: int) -> str:
    raw = f"{source_id}|{file_id}|{gen}|{start}|{end}".encode()
    return hashlib.sha256(raw).hexdigest()[:40]


def _normalize(raw: bytes) -> bytes:
    return raw if raw.endswith(b"\n") else raw + b"\n"


class LogReader:
    def __init__(
        self,
        path: Path,
        source_id: str,
        store: StateStore,
        *,
        max_lines: int = 500,
        max_wait_s: float = 1.0,
        max_batch_bytes: int = 4 * 1024 * 1024,
        max_line_bytes: int = 8 * 1024 * 1024,
        hold_open: bool | None = None,
        from_beginning: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_lines <= 0 or max_batch_bytes <= 0 or max_line_bytes <= 0 or max_wait_s < 0:
            raise ValueError("limits must be positive")
        self._path = path
        self._source_id = source_id
        self._store = store
        self._max_lines = max_lines
        self._max_wait_s = max_wait_s
        self._max_batch_bytes = max_batch_bytes
        self._max_line_bytes = max_line_bytes
        self._hold = (os.name != "nt") if hold_open is None else hold_open
        self._from_beginning = from_beginning
        self._clock = clock

        self._state = store.load()
        self.load_error = store.load_error
        self.lost_inflight = False

        self._fh: BinaryIO | None = None
        self._cur_id: str | None = self._state.file_id
        self._cur_gen = self._state.gen
        self._pos = self._state.offset
        self._seq = self._state.seq
        self._acc: list[bytes] = []
        self._acc_start = self._pos
        self._acc_bytes = 0
        self._acc_first = 0.0
        self._inflight: Batch | None = None
        if self._state.inflight is not None:
            self._restore_inflight(self._state.inflight)

    # ------------------------------------------------------------------ public
    def next_batch(self, force: bool = False) -> Batch | None:
        """The batch in flight, or a new one if it is due, or None.

        force=True cuts whatever complete lines are buffered (use at shutdown).
        """
        if self._inflight is not None:
            return self._inflight

        try:
            pst: os.stat_result | None = os.stat(self._path)
        except FileNotFoundError:
            pst = None
        path_id = file_ident(pst) if pst is not None else None

        if self._cur_id is None:
            if pst is None or path_id is None:
                return None
            self._adopt(path_id, 0 if self._from_beginning else pst.st_size)
        elif path_id is not None and path_id != self._cur_id:
            batch = self._drain_rotated(path_id)
            if batch is not None:
                return batch
        elif pst is not None and pst.st_size < self._pos:
            logger.info("file truncated, restarting at offset 0")
            if self._acc:
                batch = self._emit()
                self._restart(self._cur_gen + 1)
                return batch
            self._restart(self._cur_gen + 1)

        self._read_lines()
        if self._acc and (force or self._full() or self._due()):
            return self._emit()
        return None

    def ack(self, batch: Batch) -> None:
        """The server has the batch. Advance the saved position."""
        inflight = self._inflight
        if inflight is None or inflight.seq != batch.seq or inflight.key != batch.key:
            raise ValueError("ack for a batch that is not in flight")
        self._state.file_id = batch.file_id
        self._state.gen = batch.gen
        self._state.offset = batch.end
        self._state.seq = batch.seq
        self._state.inflight = None
        self._store.save(self._state)
        self._inflight = None

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    # ------------------------------------------------------------------ internals
    def _full(self) -> bool:
        return len(self._acc) >= self._max_lines or self._acc_bytes >= self._max_batch_bytes

    def _due(self) -> bool:
        return self._clock() - self._acc_first >= self._max_wait_s

    def _push(self, piece: bytes) -> None:
        if not self._acc:
            self._acc_first = self._clock()
            self._acc_start = self._pos
        self._acc.append(piece)
        self._acc_bytes += len(piece)
        self._pos += len(piece)

    def _emit(self) -> Batch:
        assert self._cur_id is not None
        raw = b"".join(self._acc)
        start = self._acc_start
        end = start + len(raw)
        data = _normalize(raw)
        self._seq += 1
        batch = Batch(
            seq=self._seq,
            key=make_key(self._source_id, self._cur_id, self._cur_gen, start, end),
            data=data,
            file_id=self._cur_id,
            gen=self._cur_gen,
            start=start,
            end=end,
            lines=data.count(b"\n"),
        )
        self._acc = []
        self._acc_bytes = 0
        self._acc_start = self._pos
        self._state.inflight = Inflight(
            batch.seq, batch.start, batch.end, batch.file_id, batch.gen, batch.lines
        )
        self._store.save(self._state)  # persisted BEFORE the batch is handed out
        self._inflight = batch
        return batch

    def _open(self) -> BinaryIO | None:
        if self._fh is not None:
            return self._fh
        try:
            fh = open(self._path, "rb")
        except OSError:
            return None
        if file_ident(os.fstat(fh.fileno())) != self._cur_id:
            fh.close()  # replaced between stat and open; the next poll handles it
            return None
        if self._hold:
            self._fh = fh
        return fh

    def _read_lines(self) -> None:
        """Read complete lines from the current position until a limit or EOF."""
        fh = self._open()
        if fh is None:
            return
        try:
            fh.seek(self._pos)
            while not self._full():
                line = fh.readline(self._max_line_bytes)
                if not line:
                    break
                if not line.endswith(b"\n") and len(line) < self._max_line_bytes:
                    break  # unterminated last line: wait for the rest
                self._push(line)
        finally:
            if fh is not self._fh:
                fh.close()

    def _adopt(self, file_id: str, pos: int) -> None:
        self._cur_id = file_id
        self._cur_gen = 0
        self._pos = pos
        self._acc_start = pos

    def _restart(self, gen: int) -> None:
        self.close()
        self._cur_gen = gen
        self._pos = 0
        self._acc_start = 0

    def _drain_rotated(self, new_id: str) -> Batch | None:
        """The path now holds a different file. Finish the old one, then switch."""
        if self._fh is not None:  # still the old file: read it to the very end
            self._read_lines()
            if self._full():
                return self._emit()  # more may remain; keep draining on the next call
            self._fh.seek(self._pos)
            tail = self._fh.read(self._max_line_bytes)
            if tail:
                self._push(tail)  # last line without a newline: no more is coming
        batch = self._emit() if self._acc else None
        self.close()
        self._adopt(new_id, 0)
        return batch

    def _restore_inflight(self, inf: Inflight) -> None:
        self._seq = max(self._seq, inf.seq)
        data = self._read_range(inf)
        if data is None:
            logger.warning("unacknowledged batch seq=%d cannot be re-read, dropping it", inf.seq)
            self.lost_inflight = True
            self._state.inflight = None
            self._store.save(self._state)
            return
        self._cur_id = inf.file_id
        self._cur_gen = inf.gen
        self._pos = inf.end
        self._inflight = Batch(
            seq=inf.seq,
            key=make_key(self._source_id, inf.file_id, inf.gen, inf.start, inf.end),
            data=_normalize(data),
            file_id=inf.file_id,
            gen=inf.gen,
            start=inf.start,
            end=inf.end,
            lines=inf.lines,
        )

    def _read_range(self, inf: Inflight) -> bytes | None:
        try:
            with open(self._path, "rb") as fh:
                if file_ident(os.fstat(fh.fileno())) != inf.file_id:
                    return None
                fh.seek(inf.start)
                data = fh.read(inf.end - inf.start)
        except OSError:
            return None
        return data if len(data) == inf.end - inf.start else None