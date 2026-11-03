"""Retry timing for the shipper: exponential backoff with full jitter, Retry-After support."""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def is_retryable_status(status: int) -> bool:
    """Transient server-side conditions. Other 4xx mean the request itself is wrong."""
    return status in RETRYABLE_STATUSES


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date), or None."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.isascii() and text.isdigit():
        return float(int(text)) if len(text) <= 9 else None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return max(0.0, (when - current).total_seconds())


class Backoff:
    """delay = random(0, min(cap, base * 2**attempt)), never shorter than a Retry-After."""

    def __init__(
        self,
        base_s: float = 0.5,
        cap_s: float = 60.0,
        max_retry_after_s: float = 300.0,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if base_s <= 0 or cap_s < base_s or max_retry_after_s < 0:
            raise ValueError("invalid backoff settings")
        self.base_s = base_s
        self.cap_s = cap_s
        self.max_retry_after_s = max_retry_after_s
        self._rng = rng

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        exponent = min(max(attempt, 0), 30)
        ceiling: float = min(self.cap_s, self.base_s * (2**exponent))
        jittered: float = float(self._rng()) * ceiling
        if retry_after is None:
            return jittered
        floor: float = min(float(retry_after), float(self.max_retry_after_s))
        result: float = max(jittered, floor)
        return result