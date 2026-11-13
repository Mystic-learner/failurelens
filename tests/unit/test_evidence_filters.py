from datetime import UTC, datetime

from app.services.diagnosis_runner import EVIDENCE_WINDOW, _evidence_filters


def test_filters_scope_to_service_and_window() -> None:
    ts = datetime(2026, 11, 13, 4, 54, 24, tzinfo=UTC)
    f = _evidence_filters("orders-worker", ts)
    assert f is not None
    assert f.service == "orders-worker"
    assert f.since == ts - EVIDENCE_WINDOW
    assert f.until == ts + EVIDENCE_WINDOW
    assert f.until - f.since == 2 * EVIDENCE_WINDOW


def test_missing_service_or_time_means_unscoped() -> None:
    ts = datetime(2026, 11, 13, tzinfo=UTC)
    assert _evidence_filters(None, ts) is None
    assert _evidence_filters("svc", None) is None
