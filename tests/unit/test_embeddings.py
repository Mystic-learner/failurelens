import pytest

from app.services.embeddings import (
    FakeEmbeddingProvider,
    make_provider,
)
from tests.appclient import make_settings


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.mark.asyncio
async def test_fake_is_deterministic_normalised_and_the_right_size() -> None:
    p = FakeEmbeddingProvider()
    a, b = await p.embed(["connect ECONNRESET to db", "connect ECONNRESET to db"])
    assert a == b
    assert len(a) == p.dim == 384
    assert dot(a, a) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_fake_similar_text_is_closer_than_different_text() -> None:
    p = FakeEmbeddingProvider()
    base, similar, other = await p.embed(
        [
            "connection reset by peer ECONNRESET upstream",
            "upstream ECONNRESET connection reset",
            "disk full no space left on device",
        ]
    )
    assert dot(base, similar) > dot(base, other)


@pytest.mark.asyncio
async def test_fake_handles_empty_and_unicode_text() -> None:
    p = FakeEmbeddingProvider()
    empty, uni = await p.embed(["", "\u65e5\u672c\u8a9e \U0001f525"])
    assert len(empty) == len(uni) == 384
    assert dot(empty, empty) == pytest.approx(1.0)


def test_make_provider_follows_the_settings() -> None:
    assert make_provider(make_settings(embedding_provider="fake")).name == "fake"  # type: ignore[union-attr]
    assert (
        make_provider(make_settings(embedding_provider="local", local_model_dir="no-such-dir"))
        is None
    )
