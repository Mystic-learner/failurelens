import asyncio
import logging
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings

logger = logging.getLogger(__name__)


def create_engine(settings: Settings) -> AsyncEngine:
    # pool_pre_ping makes the pool discard dead connections after a DB restart or drop,
    # so the next request reconnects instead of failing on a stale socket.
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        connect_args={"timeout": 5, "command_timeout": 30},
    )


async def check_db(engine: AsyncEngine, timeout_s: float) -> tuple[bool, float | None]:
    """Return (ok, latency_ms). Never raises: a failing database is a health result."""
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_s):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("database health check failed: %s", type(exc).__name__)
        return False, None
    return True, round((time.perf_counter() - started) * 1000, 2)
