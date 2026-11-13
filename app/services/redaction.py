"""Redaction. Runs BEFORE storage or any external call.

Placeholders look like <EMAIL>, <TOKEN>, <JWT>, <API_KEY>, <SECRET>, <CARD>, <IP_1>.
Every rule skips text that is already a placeholder, and redact() repeats until
the output stops changing, so redact(redact(x)) == redact(x).

Known trade-off: this over-redacts on purpose (e.g. 13-19 digit numbers that pass
the Luhn check, such as some epoch-millisecond values). Leaking a secret is worse.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable

MAX_TRACKED_IPS = 10_000
_MAX_PASSES = 4
_PLACEHOLDER = re.compile(r"<[A-Z][A-Z_0-9]*>")


# ---------------------------------------------------------------- split-token rules
def _make_token_rule(
    pre: str,
    charset: str,
    min_body: int,
    placeholder: str,
    check: Callable[[str], bool],
    keep_prefix: bool,
) -> tuple[re.Pattern[str], Callable[[re.Match[str]], str]]:
    """Token on one line, or split across two lines.

    The second line only counts as a continuation if it consists solely of token
    characters, so 'sk-abc\\nINFO started' is never swallowed.
    """
    pattern = re.compile(
        rf"(?P<pre>{pre})(?P<head>{charset}*)"
        rf"(?P<tail>\r?\n{charset}+(?=[ \t]*(?:\r?\n|\Z)))?"
    )

    def repl(m: re.Match[str]) -> str:
        prefix = m.group("pre") if keep_prefix else ""
        head = m.group("head")
        tail = m.group("tail") or ""
        if len(head) >= min_body and check(head):
            return prefix + placeholder + tail
        if tail:
            stripped = tail.lstrip("\r\n")
            newlines = tail[: len(tail) - len(stripped)]
            joined = head + stripped
            if len(joined) >= min_body and check(joined):
                return prefix + placeholder + newlines  # line count preserved
        return m.group(0)

    return pattern, repl


def _always(_: str) -> bool:
    return True


def _has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s)


def _jwt_ok(s: str) -> bool:
    return s.count(".") >= 2


_TOKEN_RULES: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
    # JWT: eyJ<header>.<payload>.<signature>
    _make_token_rule(r"(?<![A-Za-z0-9])eyJ", r"[A-Za-z0-9_.\-]", 20, "<JWT>", _jwt_ok, False),
    # Bearer tokens (prefix kept so the log still reads "Bearer <TOKEN>")
    _make_token_rule(
        r"(?i:(?<![A-Za-z0-9])Bearer[ \t]+)",
        r"[A-Za-z0-9._~+/=\-]",
        8,
        "<TOKEN>",
        _has_digit,
        True,
    ),
    # API keys
    _make_token_rule(r"(?<![A-Za-z0-9])sk-", r"[A-Za-z0-9_\-]", 16, "<API_KEY>", _always, False),
    _make_token_rule(r"(?<![A-Za-z0-9])AKIA", r"[0-9A-Z]", 16, "<API_KEY>", _always, False),
    _make_token_rule(
        r"(?<![A-Za-z0-9])gh[pousr]_", r"[A-Za-z0-9]", 20, "<API_KEY>", _always, False
    ),
]

# ---------------------------------------------------------------- key/value secrets
_SECRET_KEYS = (
    r"(?:password|passwd|passphrase|pwd|secret|token|api[_\-]?key|apikey|"
    r"access[_\-]?key|client[_\-]?secret|private[_\-]?key|authorization)"
)
# Matches key=value, key: value, "key": "value", 'key': 'value'.
# The lookbehind means a scan only starts at the beginning of a word, which keeps
# a 1 MB single line from going quadratic.
# An auth scheme word (Bearer/Basic/Digest) after the key is kept; the value after
# it is what gets redacted.
_KV_RE = re.compile(
    rf"(?P<k>(?<![A-Za-z0-9_.\-])[A-Za-z0-9_.\-]{{0,40}}{_SECRET_KEYS}"
    rf"[\"']?[ \t]*[=:][ \t]*)"
    rf"(?:(?P<q>[\"'])(?P<qv>(?:\\.|(?!(?P=q))[^\\])*)(?P=q)"
    rf"|(?P<scheme>(?:Bearer|Basic|Digest)[ \t]+)?(?P<uv>[^\s\"',;&)\]}}]+))",
    re.IGNORECASE,
)


# A plain word after "key:" followed by a sentence word is prose, not a secret
# (e.g. "failed to store session token: Redis is out of memory").
_PROSE_AFTER = re.compile(
    r"[ \t]+(?:is|was|are|were|has|had|not|failed|cannot|can't|expired|missing)\b", re.IGNORECASE
)


def _kv_repl(m: re.Match[str]) -> str:
    quote = m.group("q")
    value = m.group("qv") if quote else m.group("uv")
    if value == "" or _PLACEHOLDER.fullmatch(value):
        return m.group(0)
    if (
        not quote
        and not m.group("scheme")
        and value.isalpha()
        and m.group("k").rstrip(" \t").endswith(":")
        and _PROSE_AFTER.match(m.string, m.end())
    ):
        return m.group(0)
    if quote:
        return f"{m.group('k')}{quote}<SECRET>{quote}"
    return f"{m.group('k')}{m.group('scheme') or ''}<SECRET>"


# ---------------------------------------------------------------- other patterns
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,255}\.[A-Za-z]{2,24}")
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")
_IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?!\w|\.\d)")
_IPV6_RE = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}(?:[0-9A-Fa-f]{1,4}|(?<=:))(?![\w:])")
# No "=" in the lookbehind: a value right after "key=" must be examined.
_B64_RE = re.compile(r"(?<![A-Za-z0-9+/_\-])[A-Za-z0-9+/_\-]{32,}={0,2}(?![A-Za-z0-9+/=_\-])")
_HEX_ONLY = re.compile(r"[0-9a-fA-F\-]+")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _card_repl(m: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", m.group(0))
    if 13 <= len(digits) <= 19 and _luhn_ok(digits):
        return "<CARD>"
    return m.group(0)


def _b64_repl(m: re.Match[str]) -> str:
    s = m.group(0)
    if _HEX_ONLY.fullmatch(s):  # sha/md5/uuid style hashes are not secrets
        return s
    if s.count("/") > 1 or s.count("-") >= 3:  # file paths, kebab-case names
        return s
    has_upper = any(c.isupper() for c in s)
    has_lower = any(c.islower() for c in s)
    has_digit = any(c.isdigit() for c in s)
    if has_upper and has_lower and has_digit:
        return "<SECRET>"
    return s


class Redactor:
    """Holds the per-stream IP -> placeholder map so <IP_1> stays stable."""

    def __init__(self) -> None:
        self._ips: dict[str, int] = {}

    def export_state(self) -> dict[str, int]:
        return dict(self._ips)

    def import_state(self, ips: dict[str, int]) -> None:
        self._ips = {str(addr): int(n) for addr, n in ips.items()}

    def _ip_placeholder(self, addr: str) -> str:
        n = self._ips.get(addr)
        if n is None:
            if len(self._ips) >= MAX_TRACKED_IPS:
                return "<IP_MANY>"
            n = len(self._ips) + 1
            self._ips[addr] = n
        return f"<IP_{n}>"

    def _ipv4_repl(self, m: re.Match[str]) -> str:
        try:
            ip = ipaddress.IPv4Address(m.group(0))
        except ValueError:
            return m.group(0)
        return self._ip_placeholder(str(ip))

    def _ipv6_repl(self, m: re.Match[str]) -> str:
        text = m.group(0)
        if not any(c in "0123456789abcdefABCDEF" for c in text):
            return text
        try:
            ip = ipaddress.IPv6Address(text)
        except ValueError:
            return text
        return self._ip_placeholder(str(ip))

    def _one_pass(self, text: str) -> str:
        for pattern, repl in _TOKEN_RULES:
            text = pattern.sub(repl, text)
        text = _KV_RE.sub(_kv_repl, text)
        text = _EMAIL_RE.sub("<EMAIL>", text)
        text = _CARD_RE.sub(_card_repl, text)
        text = _IPV6_RE.sub(self._ipv6_repl, text)
        text = _IPV4_RE.sub(self._ipv4_repl, text)
        text = _B64_RE.sub(_b64_repl, text)
        return text

    def redact(self, text: str) -> str:
        """Redact a block of text (one line or several). Idempotent."""
        if not text or text.isspace():
            return text
        current = text
        for _ in range(_MAX_PASSES):
            nxt = self._one_pass(current)
            if nxt == current:
                break
            current = nxt
        return current


def redact(text: str) -> str:
    """Convenience wrapper with a fresh IP map."""
    return Redactor().redact(text)
