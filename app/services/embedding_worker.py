"""Asynchronous embedding: chunks are stored first, embedded later.

embed_pending() embeds one batch of chunks that have no vector yet. No database connection is
held while the provider is called. Inserts use ON CONFLICT DO NOTHING and join to chunks, so
parallel callers and chunks deleted in the meantime are harmless. A provider failure raises
EmbeddingError and leaves ingestion untouched; the chunks simply stay un-embedded and are
retried by EmbeddingWorker with exponential backoff (degraded=True meanwhile).
"""

from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.embeddings import EmbeddingError, EmbeddingProvider

logger = logging.getLogger(__name__)

MAX_EMBED_CHARS = 8000
ALLOWED_TABLES = frozenset({"chunk_embeddings_openai", "chunk_embeddings_local"})


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.8g}" for v in values) + "]"


def _table(provider: EmbeddingProvider) -> str:
    if provider.table not in ALLOWED_TABLES:
        raise ValueError(f"unknown embedding table: {provider.table!r}")
    return provider.table


async def embed_pending(
    engine: AsyncEngine, provider: EmbeddingProvider, batch_size: int = 64
) -> int:
    """Embed up to batch_size chunks without a vector. Returns how many were stored."""
    table = _table(provider)
    select = text(
        f"SELECT c.id, c.text FROM chunks c "
        f"WHERE NOT EXISTS (SELECT 1 FROM {table} e WHERE e.chunk_id = c.id) "
        f"ORDER BY c.id LIMIT CAST(:n AS integer)"
    )
    insert = text(
        f"INSERT INTO {table} (chunk_id, embedding) "
        f"SELECT r.chunk_id, CAST(r.emb AS vector) "
        f"FROM jsonb_to_recordset(CAST(CAST(:rows AS text) AS jsonb)) "
        f"AS r(chunk_id bigint, emb text) "
        f"JOIN chunks c ON c.id = r.chunk_id "
        f"ORDER BY r.chunk_id "
        f"ON CONFLICT (chunk_id) DO NOTHING RETURNING chunk_id"
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(select, {"n": batch_size})).all()
    if not rows:
        return 0

    vectors = await provider.embed([str(r[1])[:MAX_EMBED_CHARS] for r in rows])
    if len(vectors) != len(rows):
        raise EmbeddingError("provider returned the wrong number of vectors")

    pairs = zip(rows, vectors, strict=True)
    payload = [{"chunk_id": int(r[0]), "emb": vector_literal(v)} for r, v in pairs]
    async with engine.begin() as conn:
        result = await conn.execute(insert, {"rows": json.dumps(payload)})
        return len(result.all())


class EmbeddingWorker:
    def __init__(
        self,
        engine: AsyncEngine,
        provider: EmbeddingProvider,
        *,
        interval_s: float = 2.0,
        batch_size: int = 64,
        max_backoff_s: float = 60.0,
    ) -> None:
        self._engine = engine
        self._provider = provider
        self._interval_s = interval_s
        self._batch_size = batch_size
        self._max_backoff_s = max_backoff_s
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.embedded = 0
        self.failures = 0
        self.degraded = False

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run(self) -> None:
        consecutive = 0
        while not self._stop.is_set():
            try:
                stored = await embed_pending(self._engine, self._provider, self._batch_size)
            except Exception as exc:  # the worker must outlive any provider or DB failure
                consecutive += 1
                self.failures += 1
                self.degraded = True
                logger.warning("embedding batch failed: %s", type(exc).__name__)
                wait = min(self._max_backoff_s, self._interval_s * 2 ** min(consecutive, 10))
            else:
                consecutive = 0
                self.degraded = False
                self.embedded += stored
                wait = 0.0 if stored >= self._batch_size else self._interval_s
            try:
                await asyncio.wait_for(self._stop.wait(), wait)
            except TimeoutError:
                pass
