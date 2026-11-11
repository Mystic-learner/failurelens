import pytest

from app.services.latency import LatencyWindow


def test_empty_window_has_no_percentiles() -> None:
    snap = LatencyWindow().snapshot()
    assert snap == {"count": 0, "p50_s": None, "p95_s": None}


def test_nearest_rank_percentiles() -> None:
    w = LatencyWindow()
    for v in range(1, 101):
        w.record(float(v))
    assert w.percentile(0.5) == 50.0
    assert w.percentile(0.95) == 95.0
    assert w.percentile(1.0) == 100.0


def test_single_value() -> None:
    w = LatencyWindow()
    w.record(2.5)
    assert w.snapshot() == {"count": 1, "p50_s": 2.5, "p95_s": 2.5}


def test_window_is_bounded_and_negative_values_are_clamped() -> None:
    w = LatencyWindow(maxlen=3)
    for v in (10.0, 1.0, 2.0, 3.0):
        w.record(v)
    assert w.snapshot()["count"] == 3
    assert w.percentile(1.0) == 3.0
    w.record(-5.0)
    assert w.percentile(0.01) == 0.0


def test_record_since_uses_the_clock() -> None:
    w = LatencyWindow(clock=lambda: 12.0)
    w.record_since(10.0)
    assert w.percentile(0.5) == 2.0


def test_invalid_size_is_rejected() -> None:
    with pytest.raises(ValueError):
        LatencyWindow(maxlen=0)
