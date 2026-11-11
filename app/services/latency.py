"""Rolling window of recent durations (seconds), kept in memory.

Resets on restart. Percentiles use the nearest-rank method.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable


class LatencyWindow:
    def __init__(self, maxlen: int = 2000, clock: Callable[[], float] = time.monotonic) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self._values: deque[float] = deque(maxlen=maxlen)
        self._clock = clock

    def record(self, seconds: float) -> None:
        self._values.append(max(0.0, seconds))

    def record_since(self, start: float) -> None:
        self.record(self._clock() - start)

    def percentile(self, q: float) -> float | None:
        if not self._values:
            return None
        ordered = sorted(self._values)
        rank = max(1, math.ceil(q * len(ordered)))
        return ordered[rank - 1]

    def snapshot(self) -> dict[str, float | int | None]:
        return {
            "count": len(self._values),
            "p50_s": self.percentile(0.5),
            "p95_s": self.percentile(0.95),
        }
