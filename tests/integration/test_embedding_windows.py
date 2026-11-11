from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.embedding_worker import embed_pending
from app.services.embeddings import FakeEmbeddingProvider
from app.services.retrieval import search
from tests.storagetools import commit, make_chunks, setup_stream

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

RAW = "".join(f"2024-01-01 10:00:{i:02d} ERROR payment {i} failed ECONNRESET\n" for i in range(5))


class Windowed(FakeEmbeddingProvider):
    """Two windows per chunk: the whole text, then the text plus a tail."""

    def split(self, text: str) -> list[str]:
        return [text, text + " retry exhausted"]


async def store(engine: AsyncEngine) -> None:
    sid = await setup_stream(engine)
    await commit(engine, sid, "k1", 1, make_chunks(RAW))


async def rows(engine: AsyncEngine) -> list[tuple[int, int]]:
    sql = text("SELECT chunk_id, window_idx FROM chunk_embeddings_local ORDER BY 1, 2")
    async with engine.connect() as conn:
        return [(int(r[0]), int(r[1])) for r in (await conn.execute(sql)).all()]


async def test_windows_are_stored_per_chunk_and_counted_as_chunks(engine: AsyncEngine) -> None:
    await store(engine)
    provider = Windowed()
    assert await embed_pending(engine, provider) == 5
    assert await embed_pending(engine, provider) == 0
    stored = await rows(engine)
    assert len(stored) == 10
    assert {w for _, w in stored} == {0, 1}
    assert len({c for c, _ in stored}) == 5


async def test_provider_without_split_stores_window_zero_only(engine: AsyncEngine) -> None:
    await store(engine)
    assert await embed_pending(engine, FakeEmbeddingProvider()) == 5
    stored = await rows(engine)
    assert len(stored) == 5
    assert {w for _, w in stored} == {0}


async def test_search_returns_each_chunk_once_despite_several_windows(
    engine: AsyncEngine,
) -> None:
    await store(engine)
    provider = Windowed()
    await embed_pending(engine, provider)
    result = await search(
        engine,
        provider,
        "payment 3 failed ECONNRESET",
        top_k=5,
        min_similarity=-1.0,
        use_vector=True,
        use_fulltext=False,
    )
    texts = [h.text for h in result.hits]
    assert len(texts) == 5
    assert len(set(texts)) == 5
