import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.batch_worker import process_item
from app.services.ingest_queue import IngestItem

pytestmark = pytest.mark.integration

URL = "/ingest/committed"


def item(seq: int, key: str, service: str = "svc", source: str = "src") -> IngestItem:
    return IngestItem(service, source, key, seq, f"2024-01-01 10:00:0{seq} ERROR boom {seq}\n".encode())


def body(keys: list[str], service: str = "svc", source: str = "src") -> dict[str, object]:
    return {"service": service, "source_id": source, "keys": keys}


async def test_only_committed_keys_are_reported(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    await process_item(engine, item(1, "k1"))
    await process_item(engine, item(3, "k3"))  # seq 2 never committed
    resp = await client.post(URL, json=body(["k1", "k2", "k3"]))
    assert resp.status_code == 200
    assert resp.json() == {"committed": ["k1", "k3"]}


async def test_same_key_in_another_stream_does_not_count(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    await process_item(engine, item(1, "k1", "other", "other-src"))
    assert (await client.post(URL, json=body(["k1"]))).json() == {"committed": []}


async def test_unknown_stream_is_empty_not_an_error(client: httpx.AsyncClient) -> None:
    assert (await client.post(URL, json=body(["k1"]))).json() == {"committed": []}


@pytest.mark.parametrize(
    "payload",
    [{}, {"service": "s", "source_id": "x", "keys": []}, {"service": "s", "source_id": "x", "keys": [""]},
     {"service": "s", "source_id": "x", "keys": ["k"] * 1001}],
)
async def test_bad_requests_are_422(client: httpx.AsyncClient, payload: dict[str, object]) -> None:
    resp = await client.post(URL, json=payload)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"