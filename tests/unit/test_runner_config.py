from app.services.diagnosis_runner import RunnerConfig


def test_settle_covers_idle_flush() -> None:
    cfg = RunnerConfig.for_ingest(hourly_token_cap=1000, idle_s=10.0, flush_interval_s=5.0)
    assert cfg.settle_s == 16
    assert cfg.hourly_token_cap == 1000


def test_settle_rounds_up() -> None:
    cfg = RunnerConfig.for_ingest(hourly_token_cap=1, idle_s=0.2, flush_interval_s=0.2)
    assert cfg.settle_s == 2
