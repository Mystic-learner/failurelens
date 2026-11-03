import asyncio

import asyncpg
import pytest

from tests.dbtools import (
    APP_TABLES,
    MIGRATION_DB,
    dsn_for,
    recreate_database,
    run_alembic,
)

pytestmark = pytest.mark.integration


async def table_names() -> set[str]:
    conn = await asyncpg.connect(dsn_for(MIGRATION_DB), timeout=5)
    try:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    finally:
        await conn.close()
    return {r["tablename"] for r in rows}


async def test_upgrade_downgrade_upgrade_roundtrip() -> None:
    await recreate_database(MIGRATION_DB)

    await asyncio.to_thread(run_alembic, MIGRATION_DB, "upgrade", "head")
    assert set(APP_TABLES) <= await table_names()

    await asyncio.to_thread(run_alembic, MIGRATION_DB, "downgrade", "base")
    assert not set(APP_TABLES) & await table_names()

    await asyncio.to_thread(run_alembic, MIGRATION_DB, "upgrade", "head")
    assert set(APP_TABLES) <= await table_names()


async def test_upgrade_is_idempotent_at_head() -> None:
    await recreate_database(MIGRATION_DB)
    await asyncio.to_thread(run_alembic, MIGRATION_DB, "upgrade", "head")
    await asyncio.to_thread(run_alembic, MIGRATION_DB, "upgrade", "head")  # no-op, must not fail
    assert set(APP_TABLES) <= await table_names()
