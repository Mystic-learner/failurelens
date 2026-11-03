import asyncio
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.db import check_db
from app.models.schemas import (
    DatabaseHealth,
    EmbeddingHealth,
    HealthResponse,
    QueueHealth,
)

router = APIRouter()


def embedding_configured(settings: Settings) -> bool:
    """Whether the selected embedding provider can run. No external call is made."""
    if settings.embedding_provider == "openai":
        return bool(settings.openai_api_key)
    if settings.embedding_provider == "fake":
        return True
    return False  # "local": the ONNX model arrives in Phase 8


@router.get("/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
async def health(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    engine: AsyncEngine = request.app.state.engine
    queue: asyncio.Queue[object] = request.app.state.queue

    db_ok, latency_ms = await check_db(engine, settings.health_db_timeout_s)
    configured = embedding_configured(settings)

    status: Literal["ok", "degraded", "unavailable"]
    if not db_ok:
        status = "unavailable"
    elif not configured:
        status = "degraded"
    else:
        status = "ok"

    body = HealthResponse(
        status=status,
        database=DatabaseHealth(ok=db_ok, latency_ms=latency_ms),
        queue=QueueHealth(depth=queue.qsize(), max_size=settings.queue_max_size),
        embedding=EmbeddingHealth(provider=settings.embedding_provider, configured=configured),
    )
    return JSONResponse(body.model_dump(), status_code=200 if db_ok else 503)
