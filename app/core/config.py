from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "bge-small-en-v1.5"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://failurelens:failurelens@localhost:55432/failurelens"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # Largest accepted request body. A single 1 MB log line must fit, so the default is 10 MB.
    max_body_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    # Bounded ingest queue: when full (by items OR by bytes), ingest answers 429.
    queue_max_size: int = Field(default=1000, gt=0)
    queue_max_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    queue_retry_after_s: int = Field(default=1, ge=1)
    embedding_provider: Literal["openai", "local", "fake"] = "local"
    # Minimum vector similarity for a vector-only hit to count as evidence. Tuned on 11 eval
    # cases for the local bge-small model (answerable >= 0.708, off-topic <= 0.689); other
    # providers have different score distributions and need their own value.
    min_similarity: float = 0.65
    # Directory with model.onnx and tokenizer.json for the local provider.
    local_model_dir: str = str(_DEFAULT_MODEL_DIR)
    openai_api_key: str | None = None
    llm_model: str = "gpt-4.1-mini"
    # Any OpenAI-compatible chat API. Falls back to OPENAI_API_KEY when unset.
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_response_format: Literal["json_schema", "json_object"] = "json_schema"
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_hourly_token_cap: int = Field(default=200_000, gt=0)
    diagnosis_runner_enabled: bool = True
    ws_max_clients: int = Field(default=200, ge=1)
    ws_client_queue: int = Field(default=100, ge=1)
    ws_send_timeout_s: float = Field(default=5.0, gt=0)
    event_lag_s: float = Field(default=5.0, ge=0)
    event_poll_s: float = Field(default=0.5, gt=0)
    # Background embedding of stored chunks (tests turn it off and call embed_pending directly).
    embedding_worker_enabled: bool = True
    # Comma-separated list of allowed browser origins.
    cors_origins: str = "http://localhost:5173"
    health_db_timeout_s: float = Field(default=2.0, gt=0)
    # Background workers that drain the ingest queue (0 = none, used by queue-only tests).
    worker_count: int = Field(default=4, ge=0)
    # How often to look for streams whose pending record should be flushed.
    worker_flush_interval_s: float = Field(default=5.0, gt=0)
    # A stream with no new batch for this long has its pending record flushed.
    stream_idle_s: float = Field(default=10.0, ge=0)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
