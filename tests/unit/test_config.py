import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DATABASE_URL",
        "LOG_LEVEL",
        "MAX_BODY_BYTES",
        "QUEUE_MAX_SIZE",
        "EMBEDDING_PROVIDER",
        "OPENAI_API_KEY",
        "CORS_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)
    s = make()
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.queue_max_size == 1000
    assert s.embedding_provider == "local"
    assert s.max_body_bytes == 10 * 1024 * 1024
    assert s.openai_api_key is None


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUEUE_MAX_SIZE", "7")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    s = make()
    assert s.queue_max_size == 7
    assert s.embedding_provider == "fake"


def test_log_level_is_case_insensitive() -> None:
    assert make(log_level="debug").log_level == "DEBUG"


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValidationError):
        make(log_level="LOUD")


def test_invalid_provider_rejected() -> None:
    with pytest.raises(ValidationError):
        make(embedding_provider="mystery")


@pytest.mark.parametrize("field", ["queue_max_size", "max_body_bytes"])
def test_non_positive_limits_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        make(**{field: 0})


def test_cors_origin_list_splits_and_trims() -> None:
    s = make(cors_origins=" http://a.test , http://b.test,, ")
    assert s.cors_origin_list == ["http://a.test", "http://b.test"]
