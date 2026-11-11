from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.embedding_worker import EmbeddingWorker, embed_pending
from app.services.embeddings import EmbeddingError, FakeEmbeddingProvider
from tests.storagetools import commit, count, fetch_value, make_chunks, setup_stream

pytestmark = pytest.mark.asyncio

RAW = "".join(f"2024-01-01 10:00:{i:02d} ERROR payment {i} failed ECONNRESET\n" for i in range(5))


async def store(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)
    await commit(engine, sid, "k1", 1, make_chunks(RAW))
    assert await count(engine, "chunks") == 5


class Flaky(FakeEmbeddingProvider):
    def __init__(self, failures: int) -> None:
        self.failures = failures

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.failures > 0:
            self.failures -= 1
            raise EmbeddingError("provider is down", transient=True)
        return await super().embed(texts)


class DeletesAChunkMidFlight(FakeEmbeddingProvider):
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with self.engine.begin() as conn:
            await conn.execute(text("DELETE FROM chunks WHERE id = (SELECT min(id) FROM chunks)"))
        return await super().embed(texts)


class BadTable(FakeEmbeddingProvider):
    table = "chunks; DROP TABLE chunks"


async def test_embeds_every_chunk_once_and_is_idempotent(engine: AsyncEngine) -> None:
    await store(engine)
    p = FakeEmbeddingProvider()
    assert await embed_pending(engine, p) == 5
    assert await embed_pending(engine, p) == 0
    assert await count(engine, "chunk_embeddings_local") == 5
    assert (
        await fetch_value(
            engine, "SELECT vector_dims(embedding) FROM chunk_embeddings_local LIMIT 1"
        )
        == 384
    )


async def test_batch_size_limits_each_call(engine: AsyncEngine) -> None:
    await store(engine)
    p = FakeEmbeddingProvider()
    assert [await embed_pending(engine, p, 2) for _ in range(4)] == [2, 2, 1, 0]


async def test_provider_failure_leaves_chunks_stored_and_recovers(engine: AsyncEngine) -> None:
    await store(engine)
    p = Flaky(failures=1)
    with pytest.raises(EmbeddingError):
        await embed_pending(engine, p)
    assert await count(engine, "chunks") == 5
    assert await count(engine, "chunk_embeddings_local") == 0
    assert await embed_pending(engine, p) == 5


async def test_parallel_callers_never_duplicate_or_crash(engine: AsyncEngine) -> None:
    await store(engine)
    p = FakeEmbeddingProvider()
    results = await asyncio.gather(*(embed_pending(engine, p) for _ in range(5)))
    assert sum(results) == 5
    assert await count(engine, "chunk_embeddings_local") == 5


async def test_a_chunk_deleted_while_embedding_is_skipped_not_fatal(engine: AsyncEngine) -> None:
    await store(engine)
    assert await embed_pending(engine, DeletesAChunkMidFlight(engine)) == 4
    assert await count(engine, "chunk_embeddings_local") == 4


async def test_unknown_table_is_rejected(engine: AsyncEngine) -> None:
    with pytest.raises(ValueError):
        await embed_pending(engine, BadTable())
    assert await count(engine, "chunks") == 0


async def test_worker_embeds_in_the_background_and_recovers_from_failures(
    engine: AsyncEngine,
) -> None:
    await store(engine)
    worker = EmbeddingWorker(
        engine, Flaky(failures=2), interval_s=0.01, batch_size=64, max_backoff_s=0.05
    )
    worker.start()
    deadline = asyncio.get_running_loop().time() + 10
    while worker.embedded < 5:
        assert asyncio.get_running_loop().time() < deadline, "worker did not catch up"
        await asyncio.sleep(0.02)
    await worker.stop()
    assert worker.failures == 2
    assert worker.degraded is False
    assert await count(engine, "chunk_embeddings_local") == 5
    await worker.stop()  # twice is safe
