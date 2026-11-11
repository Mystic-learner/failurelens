import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

TRACE = (
    b"2024-01-01 10:00:00 ERROR request failed\n"
    b"Traceback (most recent call last):\n"
    b'  File "app.py", line 1, in <module>\n'
    b"    boom()\n"
    b"ValueError: bad\n"
)


async def upload(client: httpx.AsyncClient, data: bytes, name: str = "app.log") -> httpx.Response:
    return await client.post("/ingest", files={"file": (name, data, "text/plain")})


async def chunk_texts(engine: AsyncEngine, doc_id: int) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT text FROM chunks WHERE document_id = CAST(:d AS bigint)"), {"d": doc_id}
        )
        return [str(r[0]) for r in rows]


async def test_upload_stores_chunks_and_is_listed(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    resp = await upload(client, b"2024-01-01 10:00:00 INFO started\n2024-01-01 10:00:01 INFO ok\n")
    assert resp.status_code == 201
    body = resp.json()
    assert body["duplicate"] is False
    assert body["chunks"] >= 1
    listing = (await client.get("/documents")).json()
    assert [d["filename"] for d in listing["items"]] == ["app.log"]
    assert listing["items"][0]["chunks"] == body["chunks"]
    assert len(await chunk_texts(engine, body["id"])) == body["chunks"]


async def test_stack_trace_is_stored_whole(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    doc = (await upload(client, TRACE)).json()
    texts = await chunk_texts(engine, doc["id"])
    assert len(texts) == 1
    assert "Traceback" in texts[0]
    assert "ValueError: bad" in texts[0]


async def test_secrets_are_redacted_before_storage(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    data = b"2024-01-01 10:00:00 ERROR login failed password=hunter2 user a@b.com\n"
    doc = (await upload(client, data)).json()
    stored = " ".join(await chunk_texts(engine, doc["id"]))
    assert "hunter2" not in stored
    assert "a@b.com" not in stored
    assert "<SECRET>" in stored


async def test_crlf_file_is_normalised(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    doc = (await upload(client, TRACE.replace(b"\n", b"\r\n"))).json()
    texts = await chunk_texts(engine, doc["id"])
    assert len(texts) == 1
    assert "\r" not in texts[0]


async def test_one_megabyte_single_line_is_accepted(client: httpx.AsyncClient) -> None:
    resp = await upload(client, b"2024-01-01 10:00:00 INFO " + b"a" * 1_000_000 + b"\n")
    assert resp.status_code == 201


@pytest.mark.parametrize("data", [b"", b"   \n\t\n  "])
async def test_empty_or_whitespace_file_is_400(client: httpx.AsyncClient, data: bytes) -> None:
    assert (await upload(client, data)).status_code == 400


async def test_binary_file_is_400(client: httpx.AsyncClient) -> None:
    assert (await upload(client, bytes(range(256)) * 4)).status_code == 400


async def test_missing_file_field_is_422(client: httpx.AsyncClient) -> None:
    assert (await client.post("/ingest", data={"x": "y"})).status_code == 422


async def test_same_upload_twice_returns_the_existing_document(client: httpx.AsyncClient) -> None:
    first = (await upload(client, TRACE)).json()
    resp = await upload(client, TRACE)
    assert resp.status_code == 200
    second = resp.json()
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    assert second["chunks"] == first["chunks"]
    assert len((await client.get("/documents")).json()["items"]) == 1


async def test_same_content_under_another_name_is_a_new_document(
    client: httpx.AsyncClient,
) -> None:
    a = (await upload(client, TRACE, "a.log")).json()
    b = (await upload(client, TRACE, "b.log")).json()
    assert a["id"] != b["id"]
    assert b["chunks"] >= 1


async def test_path_in_filename_is_stripped(client: httpx.AsyncClient) -> None:
    doc = (await upload(client, TRACE, "..\\..\\etc/passwd.log")).json()
    assert doc["filename"] == "passwd.log"


async def test_delete_cascades_to_chunks(client: httpx.AsyncClient, engine: AsyncEngine) -> None:
    doc = (await upload(client, TRACE)).json()
    assert (await client.delete(f"/documents/{doc['id']}")).status_code == 204
    assert await chunk_texts(engine, doc["id"]) == []
    assert (await client.get("/documents")).json()["items"] == []


async def test_delete_missing_and_bad_ids(client: httpx.AsyncClient) -> None:
    assert (await client.delete("/documents/999999")).status_code == 404
    assert (await client.delete("/documents/0")).status_code == 422
    assert (await client.delete("/documents/abc")).status_code == 422
