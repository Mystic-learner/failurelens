from datetime import UTC, datetime

import pytest

from shipper.backoff import Backoff, is_retryable_status, parse_retry_after


def test_delay_grows_exponentially_and_is_capped() -> None:
    b = Backoff(base_s=0.5, cap_s=60.0, rng=lambda: 0.5)
    assert b.delay(0) == pytest.approx(0.25)
    assert b.delay(3) == pytest.approx(2.0)
    assert b.delay(100) == pytest.approx(30.0)
    assert b.delay(-5) == pytest.approx(0.25)


def test_jitter_stays_inside_the_ceiling() -> None:
    assert Backoff(rng=lambda: 0.0).delay(4) == 0.0
    assert Backoff(rng=lambda: 0.999999).delay(4) < 8.0


def test_retry_after_is_a_floor_and_is_clamped() -> None:
    b = Backoff(base_s=0.5, cap_s=60.0, max_retry_after_s=300.0, rng=lambda: 0.5)
    assert b.delay(0, retry_after=10.0) == 10.0
    assert b.delay(10, retry_after=1.0) == pytest.approx(30.0)
    assert b.delay(0, retry_after=99999.0) == 300.0


def test_invalid_settings_are_rejected() -> None:
    with pytest.raises(ValueError):
        Backoff(base_s=0)
    with pytest.raises(ValueError):
        Backoff(base_s=10, cap_s=1)


def test_parse_retry_after() -> None:
    now = datetime(2026, 10, 21, 7, 27, 0, tzinfo=UTC)
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(" 0 ") == 0.0
    assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", now) == 60.0
    assert parse_retry_after("Wed, 21 Oct 2026 07:00:00 GMT", now) == 0.0
    for bad in (None, "", "   ", "soon", "-5", "1.5", "9" * 5000):
        assert parse_retry_after(bad) is None


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(429, True), (503, True), (500, True), (408, True), (400, False), (401, False),
     (404, False), (413, False), (422, False), (202, False)],
)
def test_retryable_statuses(status: int, retryable: bool) -> None:
    assert is_retryable_status(status) is retryable