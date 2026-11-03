"""Fingerprinting and error-token extraction.

Both work on REDACTED text (placeholders such as <IP_1> or <SECRET> may appear).

A fingerprint identifies "the same failure": volatile parts (numbers, ids, uuids,
hex, IPs, timestamps, log level, line numbers) are replaced with <V> and the rest
is hashed. Stable error codes (HTTP 4xx/5xx, errno names, exception class names)
are added to the hash so HTTP 500 and HTTP 404 stay different failures even though
their numbers both normalise to <V>. 0x hex values are volatile (addresses), so they
are searchable in error_codes but never part of the fingerprint.

Known limits: only the first line, the exception lines and the first few stack
frames of a record are used. A failure whose only distinguishing detail is on a
later plain line gets the same fingerprint as its siblings. Hex-looking words of
4+ characters that contain a digit (e.g. "Fade2") are treated as ids.
"""

from __future__ import annotations

import hashlib
import re

MAX_SIGNATURE_FRAMES = 5
MAX_SIGNATURE_LINES = 20
MAX_ERROR_CODES = 32
MAX_SCAN_CHARS = 20_000
VOLATILE = "<V>"

# ------------------------------------------------------------------ normalisation
_TS_PREFIX_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^\W{0,2}\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?"
        r"\s*(?:Z|[+-]\d{2}(?::?\d{2})?)?[\])]?"
    ),
    re.compile(r"^\d{6} \d{6}"),  # HDFS style: 081109 203615
    re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2}[\])]?"),  # syslog
    re.compile(r"^\d{2}:\d{2}:\d{2}(?:[.,]\d+)?[\])]?"),
)
_LEVEL_PREFIX_RE = re.compile(
    r"^\s*(?:\d+\s+)?[\[(]?"
    r"(?:TRACE|DEBUG|INFO|NOTICE|WARNING|WARN|ERROR|ERR|CRITICAL|FATAL|SEVERE|PANIC)"
    r"[\])]?:?(?:\s+|$)"
)
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX0X_RE = re.compile(r"\b0[xX][0-9a-fA-F]+\b")
_IP_PLACEHOLDER_RE = re.compile(r"<IP_(?:\d+|MANY)>")
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_HEXWORD_RE = re.compile(r"(?:(?<![A-Za-z])-)?\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{4,}\b")
# A leading "-" counts as a sign only when it does not follow a letter (blk_-123 yes, node-12 no).
_NUMBER_RE = re.compile(r"(?:(?<![A-Za-z])-)?\d+")
_SPACES_RE = re.compile(r"\s+")

_FRAME_RE = re.compile(r'^\s*(?:at \S|File ")')
_EXC_LINE_RE = re.compile(
    r"^\s*(?:Caused by:|(?:[A-Za-z_][\w.$]{0,120})?(?:Error|Exception)\b)"
)


def normalize_line(line: str) -> str:
    """Replace every volatile part of one line with <V> and collapse whitespace."""
    s = _UUID_RE.sub(VOLATILE, line)
    s = _HEX0X_RE.sub(VOLATILE, s)
    s = _IP_PLACEHOLDER_RE.sub(VOLATILE, s)
    s = _IPV4_RE.sub(VOLATILE, s)
    s = _HEXWORD_RE.sub(VOLATILE, s)
    s = _NUMBER_RE.sub(VOLATILE, s)
    return _SPACES_RE.sub(" ", s).strip()


def _strip_head_prefix(line: str) -> str:
    """Remove a leading timestamp and log level from the first line of a record."""
    for pattern in _TS_PREFIX_RES:
        m = pattern.match(line)
        if m:
            line = line[m.end() :]
            break
    return _LEVEL_PREFIX_RE.sub("", line, count=1)


def signature_lines(text: str) -> list[str]:
    """The normalised lines that identify a failure."""
    lines = text.split("\n")
    out = [normalize_line(_strip_head_prefix(lines[0]))]
    frames = 0
    for line in lines[1:]:
        if len(out) >= MAX_SIGNATURE_LINES:
            break
        if _EXC_LINE_RE.match(line):
            out.append(normalize_line(line))
        elif frames < MAX_SIGNATURE_FRAMES and _FRAME_RE.match(line):
            out.append(normalize_line(line))
            frames += 1
    return [line for line in out if line]


# ------------------------------------------------------------------ error tokens
_ERRNO_NAMES = (
    "ECONNRESET", "ECONNREFUSED", "ECONNABORTED", "ETIMEDOUT", "ENOENT", "EACCES",
    "EPERM", "EEXIST", "EADDRINUSE", "EADDRNOTAVAIL", "EPIPE", "EAGAIN", "EINTR",
    "EIO", "ENOSPC", "ENOMEM", "EMFILE", "ENFILE", "EHOSTUNREACH", "ENETUNREACH",
    "ENOTFOUND", "EAI_AGAIN", "EBUSY", "EISDIR", "ENOTDIR", "ENOTEMPTY", "EINVAL",
    "EROFS", "EDQUOT",
)
_ERRNO_NAME_RE = re.compile(r"\b(" + "|".join(_ERRNO_NAMES) + r")\b")
_ERRNO_NUM_RE = re.compile(r"\berrno\s*[=:]?\s*\(?(-?\d{1,5})\b", re.IGNORECASE)
_HTTP_VERSION_RE = re.compile(r"\bHTTP/\d(?:\.\d)?\s+([45]\d\d)\b")
_HTTP_KEY_RE = re.compile(
    r'\b(?:status(?:[_ ]?code)?|code|http)"?\s*[=:]?\s*"?([45]\d\d)\b', re.IGNORECASE
)
_HEX_CODE_RE = re.compile(r"\b0[xX][0-9a-fA-F]{2,16}\b")
_EXC_NAME_RE = re.compile(r"\b[A-Za-z_][\w.$]{0,120}(?:Error|Exception)\b")


def _split_codes(text: str) -> tuple[list[str], list[str]]:
    """(stable codes, volatile 0x codes), each sorted and de-duplicated."""
    text = text[:MAX_SCAN_CHARS]
    stable: set[str] = set()
    for m in _ERRNO_NAME_RE.finditer(text):
        stable.add(m.group(1))
    for m in _ERRNO_NUM_RE.finditer(text):
        stable.add("errno_" + m.group(1).lstrip("-"))
    for m in _HTTP_VERSION_RE.finditer(text):
        stable.add("HTTP_" + m.group(1))
    for m in _HTTP_KEY_RE.finditer(text):
        stable.add("HTTP_" + m.group(1))
    for m in _EXC_NAME_RE.finditer(text):
        stable.add(m.group(0))
    hexes = {m.group(0).lower() for m in _HEX_CODE_RE.finditer(text)}
    return sorted(stable), sorted(hexes)


def extract_error_codes(text: str) -> list[str]:
    """All error tokens for the chunks.error_codes column (stable first, capped)."""
    stable, hexes = _split_codes(text)
    return (stable + hexes)[:MAX_ERROR_CODES]


def stable_error_codes(text: str) -> list[str]:
    """Error tokens that identify a failure (no volatile 0x values)."""
    return _split_codes(text)[0][:MAX_ERROR_CODES]


# ------------------------------------------------------------------ fingerprint
def make_fingerprint(text: str) -> str:
    """64-char hex sha256 identifying the failure described by one record."""
    signature = "\n".join(signature_lines(text))
    codes = ",".join(stable_error_codes(text))
    payload = signature + "\x1f" + codes
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()