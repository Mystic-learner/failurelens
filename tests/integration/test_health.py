import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.appclient import make_settings, running_client

pytestmark = pytest.mark.integration


async def test_health_ok_with_real_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert isinstance(body["database"]["latency_ms"], float)
    assert body["queue"] == {"depth": 0, "max_size": 5}
    assert body["embedding"] == {"provider": "fake", "configured": True}
    assert response.headers["x-request-id"]


async def test_health_reports_queue_depth(client: httpx.AsyncClient) -> None:
    queue = client.app.state.queue  # type: ignore[attr-defined]
    for item in range(3):
        queue.put_nowait(item)
    body = (await client.get("/health")).json()
    assert body["queue"] == {"depth": 3, "max_size": 5}


async def test_health_degraded_when_openai_key_missing(
    migrated_db_url: str, engine: AsyncEngine
) -> None:
    settings = make_settings(
        database_url=migrated_db_url, embedding_provider="openai", openai_api_key=None
    )
    async with running_client(settings) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["embedding"] == {"provider": "openai", "configured": False}


async def test_health_ok_when_openai_key_present(migrated_db_url: str, engine: AsyncEngine) -> None:
    settings = make_settings(
        database_url=migrated_db_url, embedding_provider="openai", openai_api_key="sk-test"
    )
    async with running_client(settings) as client:
        body = (await client.get("/health")).json()
    assert body["status"] == "ok"
    assert body["embedding"]["configured"] is True


async def test_health_recovers_after_connections_are_killed(
    client: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    """DB connection drop: terminate the app's pooled connections, then expect a clean reconnect."""
    assert (await client.get("/health")).status_code == 200  # warm the pool
    async with engine.connect() as admin:
        await admin.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        )
    response = await client.get("/health")  # pool_pre_ping discards the dead connections
    assert response.status_code == 200
    assert response.json()["database"]["ok"] is True
