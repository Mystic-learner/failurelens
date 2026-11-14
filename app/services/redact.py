"""Best-effort scrubbing of secrets and personal data before text goes to the LLM."""

from __future__ import annotations

import re

_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_AWS = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_KV = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|authorization)"
    r"\b(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9+/_-]{32,}")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _card_sub(m: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", m.group())
    if 13 <= len(digits) <= 19 and _luhn(digits):
        return "[REDACTED_CARD]"
    return m.group()


def redact(text: str) -> str:
    """Replace secrets and personal data. Never adds or removes lines."""
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _JWT.sub("[REDACTED_JWT]", text)
    text = _AWS.sub("[REDACTED_AWS_KEY]", text)
    text = _KV.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _IPV4.sub("[REDACTED_IP]", text)
    text = _CARD.sub(_card_sub, text)
    text = _LONG_TOKEN.sub("[REDACTED_TOKEN]", text)
    return text
