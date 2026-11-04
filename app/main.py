import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, ingest, ingest_status, search
from app.core.config import Settings, get_settings
from app.core.db import create_engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodyLimitMiddleware
from app.core.request_id import RequestIdMiddleware
from app.services.embedding_worker import EmbeddingWorker
from app.services.embeddings import make_provider
from app.services.ingest_queue import IngestQueue
from app.services.worker_pool import WorkerPool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    app.state.engine = create_engine(settings)
    app.state.queue = IngestQueue(
        max_items=settings.queue_max_size,
        max_bytes=settings.queue_max_bytes,
        retry_after_s=settings.queue_retry_after_s,
    )
    app.state.pool = WorkerPool(
        app.state.engine,
        app.state.queue,
        workers=settings.worker_count,
        flush_interval_s=settings.worker_flush_interval_s,
        idle_s=settings.stream_idle_s,
    )
    app.state.pool.start()
    app.state.embedding_provider = make_provider(settings)
    app.state.embedding_worker = None
    if app.state.embedding_provider is not None and settings.embedding_worker_enabled:
        app.state.embedding_worker = EmbeddingWorker(app.state.engine, app.state.embedding_provider)
        app.state.embedding_worker.start()
    logger.info("startup complete")
    try:
        yield
    finally:
        # Close the queue, drain every accepted batch, flush pending records, then disconnect.
        if app.state.embedding_worker is not None:
            await app.state.embedding_worker.stop()
        await app.state.pool.stop()
        await app.state.engine.dispose()
        logger.info("shutdown complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="FailureLens", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    register_exception_handlers(app)

    # Middleware added last is outermost: CORS wraps request-id, which wraps the body limit,
    # so even 413 responses carry a request id and CORS headers.
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )

    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(ingest_status.router)
    app.include_router(search.router)
    return app


app = create_app()