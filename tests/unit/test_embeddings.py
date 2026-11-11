import json

import httpx
import pytest

from app.services.embeddings import (
    EmbeddingError,
    FakeEmbeddingProvider,
    OpenAIEmbeddingProvider,
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


def openai(handler: object, retries: int = 1) -> OpenAIEmbeddingProvider:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        base_url="https://api.openai.com/v1",
    )
    return OpenAIEmbeddingProvider("sk-secret-key", http=http, retries=retries, retry_delay_s=0)


def ok(indexes: list[int]) -> httpx.Response:
    data = [{"index": i, "embedding": [float(i)] * 1536} for i in indexes]
    return httpx.Response(200, json={"data": data})


@pytest.mark.asyncio
async def test_openai_sends_model_input_and_auth_and_reorders_by_index() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return ok([1, 0])  # out of order on purpose

    vectors = await openai(handler).embed(["a", "b"])
    assert seen["path"] == "/v1/embeddings"
    assert seen["auth"] == "Bearer sk-secret-key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "text-embedding-3-small"
    assert body["input"] == ["a", "b"]
    assert vectors[0][0] == 0.0
    assert vectors[1][0] == 1.0


@pytest.mark.asyncio
async def test_openai_empty_input_makes_no_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    assert await openai(handler).embed([]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_openai_retries_once_on_transient_status(status: int) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status) if calls["n"] == 1 else ok([0])

    assert len(await openai(handler).embed(["x"])) == 1
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_openai_gives_up_after_the_retry_and_marks_the_error_transient() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429)

    with pytest.raises(EmbeddingError) as exc:
        await openai(handler).embed(["x"])
    assert exc.value.transient is True
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_openai_does_not_retry_a_permanent_error_and_never_leaks_the_key() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": "bad key sk-secret-key"})

    with pytest.raises(EmbeddingError) as exc:
        await openai(handler).embed(["x"])
    assert exc.value.transient is False
    assert calls["n"] == 1
    assert "sk-secret-key" not in str(exc.value)


@pytest.mark.asyncio
async def test_openai_network_error_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(EmbeddingError) as exc:
        await openai(handler, retries=0).embed(["x"])
    assert exc.value.transient is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 10}]}),
        httpx.Response(200, json={"data": []}),
    ],
)
async def test_openai_malformed_or_wrong_size_responses_are_errors(
    response: httpx.Response,
) -> None:
    with pytest.raises(EmbeddingError):
        await openai(lambda request: response).embed(["x"])


def test_make_provider_follows_the_settings() -> None:
    assert make_provider(make_settings(embedding_provider="fake")).name == "fake"  # type: ignore[union-attr]
    assert make_provider(make_settings(embedding_provider="openai", openai_api_key=None)) is None
    assert make_provider(make_settings(embedding_provider="local")) is None
    provider = make_provider(make_settings(embedding_provider="openai", openai_api_key="sk-x"))
    assert provider is not None
    assert provider.table == "chunk_embeddings_openai"
