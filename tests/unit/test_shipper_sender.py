import json

import httpx
import pytest

from shipper.reader import Batch
from shipper.sender import IngestClient


def batch(seq: int = 1, data: bytes = b"a\nb\n") -> Batch:
    return Batch(seq, f"k{seq}", data, "1:2", 0, 0, len(data), data.count(b"\n"))


def client_for(handler: httpx.MockTransport | object) -> IngestClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    http = httpx.AsyncClient(transport=transport, base_url="http://test")
    return IngestClient("http://test", "svc", "src", http=http)


@pytest.mark.asyncio
async def test_accepted_sends_the_headers_and_the_raw_bytes() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = request.content
        seen["headers"] = request.headers
        return httpx.Response(202, json={"status": "accepted"})

    data = b"a\r\nb \xff\x00 \xf0\x9f\x94\xa5\n"
    result = await client_for(handler).send(batch(7, data))
    assert result.kind == "accepted"
    assert seen["path"] == "/ingest/stream"
    assert seen["body"] == data
    headers = seen["headers"]
    assert isinstance(headers, httpx.Headers)
    assert headers["x-service"] == "svc"
    assert headers["x-source-id"] == "src"
    assert headers["idempotency-key"] == "k7"
    assert headers["x-sequence"] == "7"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_transient_statuses_are_retryable(status: int) -> None:
    result = await client_for(lambda request: httpx.Response(status)).send(batch())
    assert result.kind == "retry"
    assert result.status == status


@pytest.mark.asyncio
async def test_retry_after_header_is_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"})

    result = await client_for(handler).send(batch())
    assert (result.kind, result.retry_after) == ("retry", 7.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 404, 413, 422])
async def test_permanent_statuses_are_rejected(status: int) -> None:
    result = await client_for(lambda request: httpx.Response(status)).send(batch())
    assert result.kind == "rejected"
    assert result.detail == f"HTTP {status}"


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ConnectError("x"), httpx.ReadTimeout("x")])
async def test_network_errors_are_retryable_not_raised(exc: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    result = await client_for(handler).send(batch())
    assert result.kind == "retry"
    assert result.status is None


@pytest.mark.asyncio
async def test_committed_keys_posts_the_keys_and_returns_the_confirmed_ones() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"committed": ["a", "c"]})

    assert await client_for(handler).committed_keys(["a", "b", "c"]) == {"a", "c"}
    assert seen["method"] == "POST"
    assert seen["path"] == "/ingest/committed"
    assert seen["body"] == {"service": "svc", "source_id": "src", "keys": ["a", "b", "c"]}


@pytest.mark.asyncio
async def test_committed_keys_with_no_keys_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    assert await client_for(handler).committed_keys([]) == set()


@pytest.mark.asyncio
async def test_committed_keys_is_none_when_unreachable_or_nonsense() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x")

    assert await client_for(down).committed_keys(["a"]) is None
    assert await client_for(lambda r: httpx.Response(503)).committed_keys(["a"]) is None
    assert await client_for(lambda r: httpx.Response(200, content=b"not json")).committed_keys(["a"]) is None
    for body in ({}, {"committed": "a"}, {"committed": [1]}, {"committed": None}, [1]):
        got = await client_for(lambda r, b=body: httpx.Response(200, json=b)).committed_keys(["a"])
        assert got is None