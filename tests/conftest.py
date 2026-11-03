import asyncio
from collections.abc import AsyncIterator

import asyncpg
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.appclient import make_settings, running_client
from tests.dbtools import (
    ADMIN_DSN,
    APP_TABLES,
    TEST_DB,
    recreate_database,
    run_alembic,
    sqlalchemy_url_for,
)


@pytest.fixture(scope="session")
def migrated_db_url() -> str:
    """Create a fresh database and migrate it with the real alembic CLI, once per session."""
    try:
        asyncio.run(recreate_database(TEST_DB))
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.fail(
            f"Cannot reach the test Postgres at {ADMIN_DSN}: {exc!r}\n"
            "Start it with: docker compose --profile test up -d --wait db-test",
            pytrace=False,
        )
    run_alembic(TEST_DB, "upgrade", "head")
    return sqlalchemy_url_for(TEST_DB)


@pytest_asyncio.fixture
async def engine(migrated_db_url: str) -> AsyncIterator[AsyncEngine]:
    """Engine on the migrated test DB. NullPool: every task gets its own connection,
    which is what the concurrency tests need. All tables are emptied after each test."""
    eng = create_async_engine(migrated_db_url, poolclass=NullPool)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(APP_TABLES)} RESTART IDENTITY CASCADE"))
        await eng.dispose()


@pytest_asyncio.fixture
async def client(migrated_db_url: str, engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    """App client wired to the real test database with a small queue (max 5)."""
    settings = make_settings(database_url=migrated_db_url, queue_max_size=5)
    async with running_client(settings) as c:
        yield c
