"""Shared helpers for the storage tests (they need the real test Postgres)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.storage import CommitResult, commit_batch, get_or_create_stream
from app.services.stream_processor import ProcessedChunk, StreamProcessor

CLOCK = datetime(2030, 1, 1, tzinfo=UTC)  # the parser's "now"
NOW = datetime(2030, 1, 1, 12, 0, 30, tzinfo=UTC)  # the storage layer's "now"


def make_chunks(raw: str, source_id: str = "src") -> list[ProcessedChunk]:
    processor = StreamProcessor(source_id, clock=lambda: CLOCK)
    return processor.feed_text(raw) + processor.flush()


async def setup_stream(engine: AsyncEngine, service: str = "svc", source_id: str = "src") -> int:
    async with engine.begin() as conn:
        return await get_or_create_stream(conn, service, source_id)


async def commit(
    engine: AsyncEngine,
    stream_id: int,
    key: str,
    seq: int,
    chunks: list[ProcessedChunk],
    service: str = "svc",
    now: datetime = NOW,
) -> CommitResult:
    async with engine.begin() as conn:
        return await commit_batch(
            conn,
            stream_id=stream_id,
            service=service,
            idempotency_key=key,
            seq=seq,
            line_count=sum(c.chunk.line_count for c in chunks),
            chunks=chunks,
            now=now,
        )


async def fetch_all(engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    async with engine.connect() as conn:
        return list((await conn.execute(text(sql), params)).all())


async def fetch_value(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params)).scalar_one()


async def count(engine: AsyncEngine, table: str) -> int:
    return int(await fetch_value(engine, f"SELECT count(*) FROM {table}"))