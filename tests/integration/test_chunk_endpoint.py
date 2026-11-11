from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.incidents import router
from app.services.batch_worker import process_item
from app.services.ingest_queue import IngestItem

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2024, 1, 1, 10, 0, 10, tzinfo=UTC)


def make_client(engine: AsyncEngine) -> httpx.AsyncClient:
    app = FastAPI()
    app.state.engine = engine
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_chunk_endpoint_returns_text_and_rejects_bad_ids(engine: AsyncEngine) -> None:
    data = "2024-01-01 10:00:00 ERROR payment exploded badly\n2024-01-01 10:00:05 INFO ok\n"
    await process_item(engine, IngestItem("svc", "src", "k1", 1, data.encode()), now=NOW)
    async with engine.connect() as conn:
        chunk_id = (await conn.execute(text("SELECT min(id) FROM chunks"))).scalar_one()
    async with make_client(engine) as c:
        ok = await c.get(f"/chunks/{chunk_id}")
        assert ok.status_code == 200
        body = ok.json()
        assert body["id"] == chunk_id and "payment exploded badly" in body["text"]
        assert body["service"] == "svc"
        assert (await c.get("/chunks/999999")).status_code == 404
        assert (await c.get("/chunks/0")).status_code == 422
        assert (await c.get("/chunks/99999999999999999999")).status_code == 422
        assert (await c.get("/incidents/99999999999999999999")).status_code == 422
        assert (await c.get("/incidents/0")).status_code == 422
