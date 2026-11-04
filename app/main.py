import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, incidents, ingest, ingest_status, search, ws
from app.core.config import Settings, get_settings
from app.core.db import create_engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodyLimitMiddleware
from app.core.request_id import RequestIdMiddleware
from app.services import retrieval
from app.services.diagnosis import OpenAILLM
from app.services.diagnosis_runner import DiagnosisRunner, RunnerConfig
from app.services.embedding_worker import EmbeddingWorker
from app.services.embeddings import make_provider
from app.services.event_hub import EventHub
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
    app.state.diagnosis_runner = None
    app.state.diagnosis_llm = None
    app.state.diagnosis_task = None
    app.state.diagnosis_stop = asyncio.Event()
    if settings.diagnosis_runner_enabled and settings.openai_api_key:
        llm = OpenAILLM(
            settings.openai_api_key,
            model=settings.llm_model,
            timeout_s=settings.llm_timeout_s,
        )
        engine = app.state.engine
        provider = app.state.embedding_provider

        async def searcher(query: str) -> retrieval.SearchResult:
            return await retrieval.search(engine, provider, query)

        runner = DiagnosisRunner(
            engine, llm, searcher, RunnerConfig(hourly_token_cap=settings.llm_hourly_token_cap)
        )
        app.state.diagnosis_runner = runner
        app.state.diagnosis_llm = llm
        # run_forever drains pending work first, which is the cold-start recovery path.
        app.state.diagnosis_task = asyncio.create_task(runner.run_forever(app.state.diagnosis_stop))
    app.state.event_hub = EventHub(
        app.state.engine,
        poll_s=settings.event_poll_s,
        lag_s=settings.event_lag_s,
        max_clients=settings.ws_max_clients,
        queue_size=settings.ws_client_queue,
        send_timeout_s=settings.ws_send_timeout_s,
    )
    await app.state.event_hub.start()
    logger.info("startup complete")
    try:
        yield
    finally:
        # Close the queue, drain every accepted batch, flush pending records, then disconnect.
        await app.state.event_hub.stop()
        app.state.diagnosis_stop.set()
        if app.state.diagnosis_task is not None:
            await app.state.diagnosis_task
        if app.state.diagnosis_llm is not None:
            await app.state.diagnosis_llm.aclose()
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
    app.include_router(incidents.router)
    app.include_router(ws.router)
    return app


app = create_app()
