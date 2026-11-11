"""Persistent shipper state: the acknowledged position in the log and the batch in flight.

Written atomically (temp file, fsync, os.replace), so a crash leaves either the old state or
the new one, never half a file. An unreadable state file is reported through load_error and
treated as "no state": the shipper then starts from the beginning, which can duplicate lines
but never loses them.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Inflight:
    seq: int
    start: int
    end: int
    file_id: str
    gen: int
    lines: int


@dataclass
class ShipperState:
    file_id: str | None = None  # file the acknowledged offset belongs to
    gen: int = 0  # bumped when that file is truncated in place
    offset: int = 0  # first byte NOT yet acknowledged by the server
    seq: int = 0  # last acknowledged sequence number
    inflight: Inflight | None = None  # cut and persisted, not yet acknowledged


def _parse(raw: object) -> ShipperState:
    if not isinstance(raw, dict):
        raise ValueError("state is not an object")
    file_id = raw.get("file_id")
    if file_id is not None and not isinstance(file_id, str):
        raise ValueError("bad file_id")
    inflight_raw = raw.get("inflight")
    inflight = Inflight(**inflight_raw) if inflight_raw else None
    state = ShipperState(
        file_id=file_id,
        gen=int(raw["gen"]),
        offset=int(raw["offset"]),
        seq=int(raw["seq"]),
        inflight=inflight,
    )
    numbers = [state.gen, state.offset, state.seq]
    if inflight is not None:
        numbers += [inflight.seq, inflight.start, inflight.end, inflight.gen, inflight.lines]
        if inflight.end < inflight.start:
            raise ValueError("inflight range is reversed")
    if any(n < 0 for n in numbers):
        raise ValueError("negative number in state")
    return state


class StateStore:
    def __init__(self, path: Path, fsync: bool = True) -> None:
        self._path = path
        self._fsync = fsync
        self.load_error: str | None = None
        path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> ShipperState:
        self.load_error = None
        try:
            content = self._path.read_bytes()
        except FileNotFoundError:
            return ShipperState()
        except OSError as exc:
            self.load_error = f"cannot read state file: {type(exc).__name__}"
            return ShipperState()
        try:
            return _parse(json.loads(content))
        except (ValueError, KeyError, TypeError) as exc:
            self.load_error = f"unreadable state file: {type(exc).__name__}"
            return ShipperState()

    def save(self, state: ShipperState) -> None:
        tmp = self._path.with_name(self._path.name + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(json.dumps(asdict(state)).encode("utf-8"))
            fh.flush()
            if self._fsync:
                os.fsync(fh.fileno())
        os.replace(tmp, self._path)
