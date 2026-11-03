"""Decoding, line splitting, timestamp/level parsing and multi-line record grouping.

Everything here works on RAW text (before redaction). Records live only in memory;
the pipeline redacts record.text before anything is stored or sent out.
"""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo

MAX_LINE_CHARS = 4096
HEAD_LINES = 60  # first lines of a record that are always kept
TAIL_LINES = 40  # last lines of a record that are always kept
HEAD_CHARS = 10_000
TAIL_CHARS = 5_000
MAX_RECORD_CHARS = 16_000
FUTURE_TOLERANCE = timedelta(minutes=5)
ORDER_TOLERANCE = timedelta(seconds=1)
MIN_YEAR = 1990

_BINARY_SAMPLE_BYTES = 8192
_BINARY_RATIO = 0.30


# ------------------------------------------------------------------ decoding
class BinaryInputError(ValueError):
    """Input looks like a binary file, not text."""


@dataclass(frozen=True)
class DecodedText:
    text: str
    had_replacements: bool


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _suspicious_ratio(sample: str) -> float:
    if not sample:
        return 0.0
    bad = 0
    for ch in sample:
        code = ord(ch)
        if ch == "\ufffd" or code == 127 or (code < 32 and ch not in "\t\n\r\x1b"):
            bad += 1
    return bad / len(sample)


def decode_bytes(data: bytes) -> DecodedText:
    """Bytes -> clean text. Raises BinaryInputError for binary-looking input.

    Invalid UTF-8 (mixed encodings) is replaced with U+FFFD, never raised.
    NUL and other control bytes are dropped. ANSI colour codes are removed.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
        return DecodedText(_clean(text), "\ufffd" in text)
    sample = data[:_BINARY_SAMPLE_BYTES].decode("utf-8", errors="replace")
    if _suspicious_ratio(sample) > _BINARY_RATIO:
        raise BinaryInputError("input looks binary")
    text = data.decode("utf-8", errors="replace")
    return DecodedText(_clean(text), "\ufffd" in text)


def _clean(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    return _CONTROL_RE.sub("", text.replace("\x1b", ""))


def truncate_line(line: str) -> str:
    if len(line) <= MAX_LINE_CHARS:
        return line
    extra = len(line) - MAX_LINE_CHARS
    return f"{line[:MAX_LINE_CHARS]} ...[truncated {extra} chars]"


def split_lines(text: str) -> list[str]:
    """CRLF / CR / LF all split lines. A missing trailing newline is fine."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [truncate_line(line) for line in lines]


# ------------------------------------------------------------------ timestamps
_ISO_RE = re.compile(
    r"^\W{0,2}(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})"
    r"(?:[.,](\d{1,9}))?[ ]?(Z|[+-]\d{2}(?::?\d{2})?)?"
)
_COMPACT_RE = re.compile(r"^(\d{2})(\d{2})(\d{2}) (\d{2})(\d{2})(\d{2})(?!\d)")  # HDFS
_SYSLOG_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}) (\d{2}):(\d{2}):(\d{2})"
)
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )
}


def _parse_offset(text: str | None, default: tzinfo) -> tzinfo | None:
    if text is None:
        return default
    if text == "Z":
        return UTC
    sign = 1 if text[0] == "+" else -1
    digits = text[1:].replace(":", "")
    hours = int(digits[:2])
    minutes = int(digits[2:4]) if len(digits) >= 4 else 0
    if hours > 23 or minutes > 59:
        return None
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _build(
    y: int, mo: int, d: int, h: int, mi: int, s: int, frac: str | None, tz: tzinfo
) -> datetime | None:
    if s == 60:  # leap second
        s = 59
    micro = int((frac + "000000")[:6]) if frac else 0
    try:
        utc = datetime(y, mo, d, h, mi, s, micro, tzinfo=tz).astimezone(UTC)
    except (ValueError, OverflowError):
        return None
    if utc.year < MIN_YEAR:
        return None
    return utc


def parse_timestamp(line: str, default_tz: tzinfo, now: datetime) -> datetime | None:
    """Timestamp at the start of a plain-text line, normalised to UTC, or None.

    Times with an explicit offset are converted exactly. Times without one are
    assumed to be in default_tz (a fixed offset, or a ZoneInfo if you have tzdata).
    """
    m = _ISO_RE.match(line)
    if m:
        y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
        tz = _parse_offset(m.group(8), default_tz)
        if tz is None:
            return None
        return _build(y, mo, d, h, mi, s, m.group(7), tz)
    m = _COMPACT_RE.match(line)
    if m:
        yy, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
        return _build(2000 + yy, mo, d, h, mi, s, None, default_tz)
    m = _SYSLOG_RE.match(line)
    if m:
        mo = _MONTHS[m.group(1)]
        d, h, mi, s = (int(m.group(i)) for i in range(2, 6))
        dt = _build(now.year, mo, d, h, mi, s, None, default_tz)
        if dt is not None and dt > now + timedelta(days=1):
            dt = _build(now.year - 1, mo, d, h, mi, s, None, default_tz)
        return dt
    return None


# ------------------------------------------------------------------ levels
_LEVEL_MAP = {
    "TRACE": "DEBUG",
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "NOTICE": "INFO",
    "WARN": "WARN",
    "WARNING": "WARN",
    "ERROR": "ERROR",
    "ERR": "ERROR",
    "SEVERE": "ERROR",
    "CRITICAL": "FATAL",
    "FATAL": "FATAL",
    "PANIC": "FATAL",
}
_LEVEL_WORD_RE = re.compile(
    r"\b(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|CRITICAL|FATAL|SEVERE|PANIC)\b"
)
_LEVEL_KV_RE = re.compile(
    r"""\b(?:level|lvl|severity|loglevel)\s*[=:]\s*["']?([A-Za-z]+)""", re.IGNORECASE
)


def detect_level(line: str) -> str:
    head = line[:200]
    m = _LEVEL_KV_RE.search(head)
    if m:
        level = _LEVEL_MAP.get(m.group(1).upper())
        if level:
            return level
    m = _LEVEL_WORD_RE.search(head)
    if m:
        return _LEVEL_MAP[m.group(1)]
    return "UNKNOWN"


# ------------------------------------------------------------------ JSON lines
_JSON_TS_KEYS = ("timestamp", "@timestamp", "time", "ts", "datetime")
_JSON_LEVEL_KEYS = ("level", "severity", "levelname", "lvl", "loglevel")


def _json_ts(value: object, default_tz: tzinfo) -> datetime | None:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            seconds = float(value)
            if seconds > 1e11:  # milliseconds
                seconds /= 1000
            dt = datetime.fromtimestamp(seconds, UTC)
        elif isinstance(value, str):
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=default_tz)
            dt = dt.astimezone(UTC)
        else:
            return None
    except (ValueError, OverflowError, OSError):
        return None
    return dt if dt.year >= MIN_YEAR else None


def parse_json_line(
    line: str, default_tz: tzinfo
) -> tuple[datetime | None, str | None] | None:
    """(timestamp, level) of a JSON log line, or None if it is not a JSON object."""
    try:
        data = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    ts: datetime | None = None
    for key in _JSON_TS_KEYS:
        if key in data:
            ts = _json_ts(data[key], default_tz)
            if ts is not None:
                break
    level: str | None = None
    for key in _JSON_LEVEL_KEYS:
        value = data.get(key)
        if isinstance(value, str):
            level = _LEVEL_MAP.get(value.upper())
            if level:
                break
    return ts, level


# ------------------------------------------------------------------ records
@dataclass(frozen=True)
class LogRecord:
    text: str  # all lines of the record joined with "\n"
    level: str  # DEBUG | INFO | WARN | ERROR | FATAL | UNKNOWN
    timestamp: datetime | None  # UTC
    flags: tuple[str, ...]  # no_timestamp, future_timestamp, out_of_order, stack_trace, truncated
    line_count: int  # original number of lines, before any truncation


# A line that belongs to the previous record even though it has no timestamp.
_TRACE_HINT_RE = re.compile(
    r'^(?:\s*at \S|\s*File "|Traceback \(most recent|Caused by:|Exception in thread'
    r"|panic:|goroutine \d|[A-Za-z_][\w.$]*(?:Error|Exception)\b)"
)


def _is_continuation(line: str) -> bool:
    return line[0] in " \t" or _TRACE_HINT_RE.match(line) is not None


class _Pending:
    """A record being built. Memory is bounded: HEAD_LINES + TAIL_LINES lines."""

    def __init__(self, first_line: str, ts: datetime | None, level: str) -> None:
        self.head: list[str] = [first_line]
        self.tail: deque[str] = deque(maxlen=TAIL_LINES)
        self.omitted = 0
        self.count = 1
        self.ts = ts
        self.level = level
        self.has_trace = _TRACE_HINT_RE.match(first_line) is not None

    def add(self, line: str) -> None:
        self.count += 1
        if _TRACE_HINT_RE.match(line):
            self.has_trace = True
        if len(self.head) < HEAD_LINES:
            self.head.append(line)
            return
        if len(self.tail) == TAIL_LINES:
            self.omitted += 1
        self.tail.append(line)


class StreamParser:
    """One parser per log stream. Holds the unfinished record between batches.

    feed() returns the records that are complete. The last record stays pending
    (a stack trace may continue in the next batch) until more lines arrive or
    flush() is called, e.g. after an idle timeout or at shutdown.
    """

    def __init__(
        self,
        default_tz: tzinfo = UTC,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._tz = default_tz
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._max_ts: datetime | None = None
        self._pending: _Pending | None = None

    def feed(self, lines: list[str]) -> list[LogRecord]:
        now = self._clock()
        done: list[LogRecord] = []
        for line in lines:
            if not line.strip():
                continue
            ts: datetime | None = None
            json_level: str | None = None
            is_json = False
            if line.startswith("{"):
                meta = parse_json_line(line, self._tz)
                if meta is not None:
                    is_json = True
                    ts, json_level = meta
            if not is_json:
                ts = parse_timestamp(line, self._tz, now)
            starts_new = (
                is_json
                or ts is not None
                or self._pending is None
                or not _is_continuation(line)
            )
            if starts_new:
                if self._pending is not None:
                    done.append(self._finish(self._pending, now))
                self._pending = _Pending(line, ts, json_level or detect_level(line))
            elif self._pending is not None:
                self._pending.add(line)
        return done

    def flush(self) -> list[LogRecord]:
        if self._pending is None:
            return []
        record = self._finish(self._pending, self._clock())
        self._pending = None
        return [record]

    def _finish(self, pending: _Pending, now: datetime) -> LogRecord:
        lines = list(pending.head)
        if pending.omitted:
            lines.append(f"... [{pending.omitted} lines truncated] ...")
        lines.extend(pending.tail)
        text = "\n".join(lines)
        flags: list[str] = []

        if pending.ts is None:
            flags.append("no_timestamp")
        elif pending.ts > now + FUTURE_TOLERANCE:
            flags.append("future_timestamp")
        else:
            if self._max_ts is not None and pending.ts < self._max_ts - ORDER_TOLERANCE:
                flags.append("out_of_order")
            if self._max_ts is None or pending.ts > self._max_ts:
                self._max_ts = pending.ts

        truncated = pending.omitted > 0
        if len(text) > MAX_RECORD_CHARS:
            text = f"{text[:HEAD_CHARS]}\n... [truncated] ...\n{text[-TAIL_CHARS:]}"
            truncated = True
        if truncated:
            flags.append("truncated")
        if pending.has_trace:
            flags.append("stack_trace")

        level = pending.level
        if level == "UNKNOWN" and pending.has_trace:
            level = "ERROR"
        return LogRecord(
            text=text,
            level=level,
            timestamp=pending.ts,
            flags=tuple(flags),
            line_count=pending.count,
        )