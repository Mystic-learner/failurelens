import string
from datetime import datetime

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.services.fingerprint import (
    MAX_ERROR_CODES,
    MAX_SIGNATURE_LINES,
    extract_error_codes,
    make_fingerprint,
    signature_lines,
)

stamps = st.datetimes(min_value=datetime(2000, 1, 1), max_value=datetime(2099, 12, 31))
numbers = st.integers(min_value=0, max_value=10**12)
volatile_values = st.tuples(
    stamps,
    numbers,
    st.ip_addresses(v=4).map(str),
    st.uuids().map(str),
    st.integers(min_value=1, max_value=2**63),
    numbers,
)
words = st.text(alphabet=string.ascii_lowercase, min_size=6, max_size=20)


def render(values: tuple[datetime, int, str, str, int, int]) -> str:
    ts, order_id, ip, uid, hx, ms = values
    return (
        f"{ts:%Y-%m-%d %H:%M:%S} ERROR payment {order_id} failed for request {uid} "
        f"from {ip} at 0x{hx:x} after {ms}ms"
    )


@settings(max_examples=300, deadline=None)
@given(volatile_values, volatile_values)
def test_same_failure_same_fingerprint(
    a: tuple[datetime, int, str, str, int, int], b: tuple[datetime, int, str, str, int, int]
) -> None:
    assert make_fingerprint(render(a)) == make_fingerprint(render(b))


@settings(max_examples=300, deadline=None)
@given(words, words)
def test_different_failures_different_fingerprint(a: str, b: str) -> None:
    assume(a != b)
    assert make_fingerprint(f"ERROR failed to {a} the thing") != make_fingerprint(
        f"ERROR failed to {b} the thing"
    )


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=400))
def test_arbitrary_text_is_safe_and_deterministic(text: str) -> None:
    first = make_fingerprint(text)
    assert first == make_fingerprint(text)
    assert len(first) == 64
    assert len(extract_error_codes(text)) <= MAX_ERROR_CODES
    assert len(signature_lines(text)) <= MAX_SIGNATURE_LINES
