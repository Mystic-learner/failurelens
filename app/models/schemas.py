from typing import Literal

from pydantic import BaseModel


class DatabaseHealth(BaseModel):
    ok: bool
    latency_ms: float | None


class QueueHealth(BaseModel):
    depth: int
    max_size: int


class EmbeddingHealth(BaseModel):
    provider: str
    configured: bool


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    database: DatabaseHealth
    queue: QueueHealth
    embedding: EmbeddingHealth
