from collections.abc import AsyncIterator

import httpx
import pytest

from app.services.ingest_queue import IngestItem, IngestQueue
from tests.appclient import make_settings, running_client

pytestmark = pytest.mark.integration

URL = "/ingest/stream"


def headers(seq: int = 1, key: str = "k1", **extra: str) -> dict[str, str]:
    base = {
        "X-Service": "svc",
        "X-Source-Id": "src",
        "Idempotency-Key": key,
        "X-Sequence": str(seq),
    }
    base.update(extra)
    return base


def queue_of(client: httpx.AsyncClient) -> IngestQueue:
    queue: IngestQueue = client.app.state.queue  # type: ignore[attr-defined]
    return queue


async def test_valid_batch_is_accepted_and_queued(client: httpx.AsyncClient) -> None:
    response = await client.post(
        URL, content=b"2024-01-01 10:00:00 ERROR boom\n", headers=headers()
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "queue_depth": 1}
    assert response.headers["x-request-id"]

    item = await queue_of(client).get()
    assert item is not None
    assert (item.service, item.source_id, item.idempotency_key, item.seq) == ("svc", "src", "k1", 1)
    assert item.data == b"2024-01-01 10:00:00 ERROR boom\n"


async def test_empty_body_is_400(client: httpx.AsyncClient) -> None:
    response = await client.post(URL, content=b"", headers=headers())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    assert queue_of(client).depth == 0


async def test_whitespace_only_body_is_accepted(client: httpx.AsyncClient) -> None:
    response = await client.post(URL, content=b"  \n\t\r\n ", headers=headers())
    assert response.status_code == 202
    assert queue_of(client).depth == 1


@pytest.mark.parametrize("missing", ["X-Service", "X-Source-Id", "Idempotency-Key", "X-Sequence"])
async def test_missing_header_is_422(client: httpx.AsyncClient, missing: str) -> None:
    h = headers()
    del h[missing]
    response = await client.post(URL, content=b"x", headers=h)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert queue_of(client).depth == 0


@pytest.mark.parametrize("seq", ["-1", "abc", "1.5", ""])
async def test_bad_sequence_is_422(client: httpx.AsyncClient, seq: str) -> None:
    h = headers()
    h["X-Sequence"] = seq
    response = await client.post(URL, content=b"x", headers=h)
    assert response.status_code == 422


async def test_blank_header_value_is_422(client: httpx.AsyncClient) -> None:
    response = await client.post(URL, content=b"x", headers=headers(**{"X-Service": "   "}))
    assert response.status_code == 422
    assert queue_of(client).depth == 0


async def test_overlong_header_is_422(client: httpx.AsyncClient) -> None:
    response = await client.post(URL, content=b"x", headers=headers(key="k" * 201))
    assert response.status_code == 422


async def test_full_queue_is_429_with_retry_after(client: httpx.AsyncClient) -> None:
    for i in range(5):
        assert (
            await client.post(URL, content=b"x", headers=headers(i, f"k{i}"))
        ).status_code == 202
    response = await client.post(URL, content=b"x", headers=headers(9, "k9"))
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["code"] == "too_many_requests"
    assert queue_of(client).depth == 5


async def test_queue_accepts_again_after_it_drains(client: httpx.AsyncClient) -> None:
    for i in range(5):
        await client.post(URL, content=b"x", headers=headers(i, f"k{i}"))
    assert (await client.post(URL, content=b"x", headers=headers(9, "k9"))).status_code == 429
    assert await queue_of(client).get() is not None
    assert (await client.post(URL, content=b"x", headers=headers(9, "k9"))).status_code == 202


async def test_closed_queue_is_503_with_retry_after(client: httpx.AsyncClient) -> None:
    queue_of(client).close()
    response = await client.post(URL, content=b"x", headers=headers())
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


async def test_binary_and_invalid_utf8_bytes_reach_the_queue_untouched(
    client: httpx.AsyncClient,
) -> None:
    payload = b"\x00\x01\xff\xfe caf\xe9 \x80\x81"
    response = await client.post(URL, content=payload, headers=headers())
    assert response.status_code == 202
    item = await queue_of(client).get()
    assert item is not None
    assert item.data == payload


async def test_one_megabyte_single_line_is_accepted(client: httpx.AsyncClient) -> None:
    payload = b"2024-01-01 10:00:00 INFO " + b"a" * 1_000_000
    response = await client.post(URL, content=payload, headers=headers())
    assert response.status_code == 202
    item = await queue_of(client).get()
    assert item is not None
    assert len(item.data) == len(payload)


async def test_body_over_the_limit_is_413(migrated_db_url: str) -> None:
    settings = make_settings(database_url=migrated_db_url, max_body_bytes=1000)
    async with running_client(settings) as client:
        response = await client.post(URL, content=b"x" * 1001, headers=headers())
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"
        assert queue_of(client).depth == 0
        ok = await client.post(URL, content=b"x" * 1000, headers=headers(2, "k2"))
        assert ok.status_code == 202


async def test_chunked_body_over_the_limit_without_content_length(
    migrated_db_url: str,
) -> None:
    """Probe: no Content-Length, so the limit is enforced by counting streamed bytes."""
    settings = make_settings(database_url=migrated_db_url, max_body_bytes=1000)

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(5):
            yield b"x" * 300

    async with running_client(settings) as client:
        response = await client.post(URL, content=chunks(), headers=headers())
        assert response.status_code == 413
        assert queue_of(client).depth == 0


async def test_error_responses_never_echo_the_body(client: httpx.AsyncClient) -> None:
    secret = b"password=hunter2 sk-proj1234567890abcdefghij"
    h = headers()
    del h["X-Sequence"]
    response = await client.post(URL, content=secret, headers=h)
    assert response.status_code == 422
    assert b"hunter2" not in response.content
    assert b"sk-proj" not in response.content


async def test_wrong_method_is_405(client: httpx.AsyncClient) -> None:
    response = await client.get(URL)
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_queue_never_grows_past_its_bound_under_a_flood(client: httpx.AsyncClient) -> None:
    statuses = [
        (await client.post(URL, content=b"x" * 100, headers=headers(i, f"k{i}"))).status_code
        for i in range(200)
    ]
    assert statuses.count(202) == 5
    assert statuses.count(429) == 195
    assert queue_of(client).depth == 5
    assert queue_of(client).queued_bytes == 500


async def test_queue_item_type_roundtrip() -> None:
    item = IngestItem("s", "o", "k", 3, b"abc")
    assert item.size == 3
