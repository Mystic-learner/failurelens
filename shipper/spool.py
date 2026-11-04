"""On-disk queue of batches that are cut but not yet confirmed committed by the server.

One file per batch, named by sequence number: <seq:020d>.batch
File layout: one JSON header line, then the raw batch bytes. The header carries the batch
metadata and the data length, so a truncated file is detected and quarantined (renamed to
.corrupt) instead of being sent. The idempotency key of every spooled batch is also kept in
memory (read from the header at startup) so the forwarder can ask the server which keys are
committed without re-reading the batch files.

Files are written atomically (temp file, fsync, os.replace). The spool is bounded: put()
returns False when it is full (an oversized batch is still accepted into an EMPTY spool so
progress is always possible). Delivery is in sequence order.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from shipper.reader import Batch

logger = logging.getLogger("failurelens.shipper")

_SUFFIX = ".batch"
_HEADER_MAX = 65_536


def _name(seq: int) -> str:
    return f"{seq:020d}{_SUFFIX}"


def _read_key(path: Path) -> str:
    with open(path, "rb") as fh:
        header_raw = fh.readline(_HEADER_MAX)
    header = json.loads(header_raw)
    if not isinstance(header, dict):
        raise ValueError("bad header")
    return str(header["key"])


def _read(path: Path) -> Batch:
    raw = path.read_bytes()
    header_raw, sep, data = raw.partition(b"\n")
    if not sep:
        raise ValueError("no header")
    header = json.loads(header_raw)
    if not isinstance(header, dict) or len(data) != int(header["size"]):
        raise ValueError("size mismatch")
    return Batch(
        seq=int(header["seq"]),
        key=str(header["key"]),
        data=data,
        file_id=str(header["file_id"]),
        gen=int(header["gen"]),
        start=int(header["start"]),
        end=int(header["end"]),
        lines=int(header["lines"]),
    )


class Spool:
    def __init__(self, directory: Path, max_bytes: int = 256 * 1024 * 1024, fsync: bool = True):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._dir = directory
        self._max = max_bytes
        self._fsync = fsync
        self.corrupt: list[str] = []
        self._sizes: dict[int, int] = {}
        self._keys: dict[int, str] = {}
        directory.mkdir(parents=True, exist_ok=True)
        for tmp in directory.glob("*.tmp"):
            tmp.unlink(missing_ok=True)  # half-written file from a crash
        for path in directory.glob(f"*{_SUFFIX}"):
            try:
                seq = int(path.stem)
                key = _read_key(path)
                size = path.stat().st_size
            except (ValueError, KeyError, TypeError, OSError):
                self._quarantine(path)
                continue
            self._sizes[seq] = size
            self._keys[seq] = key

    def __len__(self) -> int:
        return len(self._sizes)

    @property
    def bytes_used(self) -> int:
        return sum(self._sizes.values())

    def pending_seqs(self) -> list[int]:
        return sorted(self._sizes)

    def pending(self) -> list[tuple[int, str]]:
        """(seq, idempotency key) of every spooled batch, oldest first."""
        return [(seq, self._keys[seq]) for seq in sorted(self._sizes)]

    def put(self, batch: Batch) -> bool:
        """Store the batch durably. False if the spool is full. Same seq twice is a no-op."""
        if batch.seq in self._sizes:
            return True
        header = json.dumps(
            {
                "seq": batch.seq,
                "key": batch.key,
                "file_id": batch.file_id,
                "gen": batch.gen,
                "start": batch.start,
                "end": batch.end,
                "lines": batch.lines,
                "size": len(batch.data),
            }
        ).encode("utf-8")
        payload = header + b"\n" + batch.data
        if self._sizes and self.bytes_used + len(payload) > self._max:
            return False
        final = self._dir / _name(batch.seq)
        tmp = final.with_name(final.name + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(payload)
            fh.flush()
            if self._fsync:
                os.fsync(fh.fileno())
        os.replace(tmp, final)
        self._sizes[batch.seq] = len(payload)
        self._keys[batch.seq] = batch.key
        return True

    def peek(self) -> Batch | None:
        """The oldest batch, or None. Unreadable files are quarantined and skipped."""
        for seq in self.pending_seqs():
            path = self._dir / _name(seq)
            try:
                return _read(path)
            except (OSError, ValueError, KeyError, TypeError):
                self._forget(seq)
                self._quarantine(path)
        return None

    def get(self, seq: int) -> Batch | None:
        """The batch with this sequence number, or None. Unreadable files are quarantined."""
        if seq not in self._sizes:
            return None
        path = self._dir / _name(seq)
        try:
            return _read(path)
        except (OSError, ValueError, KeyError, TypeError):
            self._forget(seq)
            self._quarantine(path)
            return None

    def pop(self, seq: int) -> None:
        self._forget(seq)
        (self._dir / _name(seq)).unlink(missing_ok=True)

    def prune_through(self, committed_seq: int) -> int:
        """Delete every batch with seq <= committed_seq. Returns how many were removed."""
        done = [s for s in self._sizes if s <= committed_seq]
        for seq in done:
            self.pop(seq)
        return len(done)

    def _forget(self, seq: int) -> None:
        self._sizes.pop(seq, None)
        self._keys.pop(seq, None)

    def _quarantine(self, path: Path) -> None:
        target = path.with_name(path.name + ".corrupt")
        try:
            os.replace(path, target)
        except OSError:
            path.unlink(missing_ok=True)
        self.corrupt.append(path.name)
        logger.warning("spool file %s is unreadable and was quarantined", path.name)

    def key_of(self, seq: int) -> str | None:
        """Idempotency key of a spooled batch, reading only the header line."""
        if seq not in self._sizes:
            return None
        try:
            with open(self._dir / _name(seq), "rb") as fh:
                header = json.loads(fh.readline())
            return str(header["key"])
        except (OSError, ValueError, KeyError, TypeError):
            return None