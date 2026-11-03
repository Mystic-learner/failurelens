"""Helpers for tests that need the real Postgres started by `docker compose --profile test`."""

import os
import subprocess
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
ADMIN_DSN = os.environ.get(
    "TEST_DB_ADMIN_DSN", "postgresql://failurelens:failurelens@127.0.0.1:55433/postgres"
)
TEST_DB = "fl_test"
MIGRATION_DB = "fl_migration_test"

APP_TABLES = [
    "streams",
    "ingest_batches",
    "documents",
    "chunks",
    "chunk_embeddings_openai",
    "chunk_embeddings_local",
    "signatures",
    "signature_buckets",
    "incidents",
    "events",
    "llm_usage",
]


def dsn_for(database: str) -> str:
    base, _ = ADMIN_DSN.rsplit("/", 1)
    return f"{base}/{database}"


def sqlalchemy_url_for(database: str) -> str:
    return "postgresql+asyncpg://" + dsn_for(database).split("://", 1)[1]


async def recreate_database(database: str) -> None:
    conn = await asyncpg.connect(ADMIN_DSN, timeout=5)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{database}"')
    finally:
        await conn.close()


def run_alembic(database: str, *args: str) -> None:
    """Run the real alembic CLI in a subprocess against the given database."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": sqlalchemy_url_for(database)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
        )


def vector_literal(dim: int, value: float = 0.1) -> str:
    return "[" + ",".join([str(value)] * dim) + "]"
