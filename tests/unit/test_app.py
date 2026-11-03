import asyncio

import httpx

from app.core.db import check_db
from tests.appclient import make_settings, running_client

# Nothing listens on port 1, so connections are refused immediately. No database is needed.
UNREACHABLE_DB = "postgresql+asyncpg://u:p@127.0.0.1:1/none"


class SlowConnection:
    async def __aenter__(self) -> None:
        await asyncio.sleep(5)

    async def __aexit__(self, *exc: object) -> None:
        return None


class SlowEngine:
    def connect(self) -> SlowConnection:
        return SlowConnection()


async def test_check_db_times_out_instead_of_hanging() -> None:
    ok, latency = await check_db(SlowEngine(), 0.05)  # type: ignore[arg-type]
    assert (ok, latency) == (False, None)


async def test_health_is_503_when_the_database_is_unreachable() -> None:
    async with running_client(make_settings(database_url=UNREACHABLE_DB)) as client:
        response = await client.get("/health")
    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "unavailable"
    assert body["database"] == {"ok": False, "latency_ms": None}
    assert body["queue"] == {"depth": 0, "max_size": 1000}
    assert body["embedding"] == {"provider": "fake", "configured": True}


async def test_unknown_route_returns_the_json_error_shape() -> None:
    async with running_client(make_settings(database_url=UNREACHABLE_DB)) as client:
        response = await client.get("/nope")
    assert response.status_code == 404
    assert set(response.json()["error"]) == {"code", "message", "request_id"}


async def test_oversize_body_gets_413_before_routing() -> None:
    settings = make_settings(database_url=UNREACHABLE_DB, max_body_bytes=100)
    async with running_client(settings) as client:
        response = await client.post("/health", content=b"x" * 101)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def _cors(client: httpx.AsyncClient, origin: str) -> httpx.Response:
    return await client.options(
        "/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


async def test_cors_allows_configured_origin_only() -> None:
    settings = make_settings(database_url=UNREACHABLE_DB, cors_origins="http://good.test")
    async with running_client(settings) as client:
        allowed = await _cors(client, "http://good.test")
        denied = await _cors(client, "http://evil.test")
    assert allowed.headers["access-control-allow-origin"] == "http://good.test"
    assert "access-control-allow-origin" not in denied.headers
