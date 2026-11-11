from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest

from app.services.embedding_worker import ALLOWED_TABLES
from app.services.embeddings import EmbeddingError

local = pytest.importorskip("app.services.embeddings_local")

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "bge-small-en-v1.5"
needs_model = pytest.mark.skipif(
    not local.model_files_present(MODEL_DIR), reason="model files not downloaded"
)


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def norm(v: list[float]) -> float:
    return math.sqrt(dot(v, v))


@pytest.fixture(scope="module")
def provider() -> object:
    return local.LocalEmbeddingProvider(MODEL_DIR)


def test_model_files_present_helper(tmp_path: Path) -> None:
    assert local.model_files_present(tmp_path) is False
    (tmp_path / "model.onnx").write_bytes(b"x")
    assert local.model_files_present(tmp_path) is False
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    assert local.model_files_present(tmp_path) is True


def test_missing_model_dir_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(EmbeddingError, match="model files not found"):
        local.LocalEmbeddingProvider(tmp_path)


def test_invalid_batch_size_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        local.LocalEmbeddingProvider(tmp_path, batch_size=0)


@needs_model
def test_provider_attributes_match_the_schema(provider: object) -> None:
    assert provider.name == "local"  # type: ignore[attr-defined]
    assert provider.dim == 384  # type: ignore[attr-defined]
    assert provider.table == "chunk_embeddings_local"  # type: ignore[attr-defined]
    assert provider.table in ALLOWED_TABLES  # type: ignore[attr-defined]


@needs_model
@pytest.mark.asyncio
async def test_empty_input_returns_empty_list(provider: object) -> None:
    assert await provider.embed([]) == []  # type: ignore[attr-defined]


@needs_model
@pytest.mark.asyncio
async def test_vectors_have_384_dims_and_unit_norm(provider: object) -> None:
    vectors = await provider.embed(["connection refused", "disk full", "timeout"])  # type: ignore[attr-defined]
    assert len(vectors) == 3
    for vec in vectors:
        assert len(vec) == 384
        assert norm(vec) == pytest.approx(1.0, abs=1e-4)


@needs_model
@pytest.mark.asyncio
async def test_related_text_scores_closer_than_unrelated(provider: object) -> None:
    query = "database connection refused"
    related = "ERROR connection refused to database postgres:5432 while handling request"
    unrelated = "the cat sat quietly on the warm sofa all afternoon"
    q, rel, unrel = await provider.embed([query, related, unrelated])  # type: ignore[attr-defined]
    assert dot(q, rel) > dot(q, unrel)


@needs_model
@pytest.mark.asyncio
async def test_order_is_preserved_across_sub_batches(provider: object) -> None:
    texts = [f"line {i}: " + "word " * (i * 7) for i in range(20)]  # 20 > batch size 16
    full = await provider.embed(texts)  # type: ignore[attr-defined]
    assert len(full) == 20
    for i in (0, 7, 15, 19):
        single = (await provider.embed([texts[i]]))[0]  # type: ignore[attr-defined]
        assert dot(full[i], single) == pytest.approx(1.0, abs=1e-4)


@needs_model
@pytest.mark.asyncio
async def test_long_input_is_truncated_not_rejected(provider: object) -> None:
    (vec,) = await provider.embed(["word " * 5000])  # type: ignore[attr-defined]
    assert len(vec) == 384
    assert norm(vec) == pytest.approx(1.0, abs=1e-4)


@needs_model
@pytest.mark.asyncio
async def test_blank_and_non_ascii_text(provider: object) -> None:
    vectors = await provider.embed(["", "   ", "données invalides ✓ 日本語"])  # type: ignore[attr-defined]
    assert len(vectors) == 3
    for vec in vectors:
        assert all(math.isfinite(x) for x in vec)
        assert norm(vec) == pytest.approx(1.0, abs=1e-4)


@needs_model
@pytest.mark.asyncio
async def test_concurrent_calls_match_sequential_results() -> None:
    fresh = local.LocalEmbeddingProvider(MODEL_DIR)
    texts = ["connection refused", "out of memory", "disk full", "permission denied"]
    parallel = await asyncio.gather(*(fresh.embed([t]) for t in texts))
    for text, result in zip(texts, parallel, strict=True):
        expected = (await fresh.embed([text]))[0]
        assert dot(result[0], expected) == pytest.approx(1.0, abs=1e-4)


@needs_model
def test_split_keeps_short_text_whole(provider) -> None:
    assert provider.split("one line\nsecond line") == ["one line\nsecond line"]


@needs_model
def test_split_cuts_long_text_into_line_aligned_windows(provider) -> None:
    lines = [f"line {i}: connection refused to database host {i}" for i in range(200)]
    windows = provider.split("\n".join(lines))
    assert len(windows) > 1
    assert "\n".join(windows).splitlines() == lines


@needs_model
def test_split_never_returns_nothing(provider) -> None:
    assert provider.split("") == [""]
    assert provider.split("x" * 20000) == ["x" * 20000]
