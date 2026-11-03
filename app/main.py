import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.core.config import Settings, get_settings
from app.core.db import create_engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodyLimitMiddleware
from app.core.request_id import RequestIdMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    app.state.engine = create_engine(settings)
    app.state.queue = asyncio.Queue(maxsize=settings.queue_max_size)
    logger.info("startup complete")
    try:
        yield
    finally:
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
    return app


app = create_app()
