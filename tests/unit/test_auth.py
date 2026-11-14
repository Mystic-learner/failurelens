from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI

from app.core.auth import require_api_key
from app.core.config import Settings
from app.main import create_app


def _client(api_key):
    app = FastAPI()
    app.state.settings = SimpleNamespace(api_key=api_key)
    router = APIRouter(dependencies=[Depends(require_api_key)])

    @router.get("/x")
    async def x():
        return {"ok": True}

    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.mark.parametrize("key", [None, ""])
async def test_disabled_when_no_key_configured(key):
    async with _client(key) as c:
        assert (await c.get("/x")).status_code == 200


async def test_missing_key_is_401():
    async with _client("s3cret") as c:
        assert (await c.get("/x")).status_code == 401


async def test_wrong_key_is_401():
    async with _client("s3cret") as c:
        assert (await c.get("/x", headers={"X-API-Key": "nope"})).status_code == 401


async def test_x_api_key_accepted():
    async with _client("s3cret") as c:
        assert (await c.get("/x", headers={"X-API-Key": "s3cret"})).status_code == 200


async def test_bearer_accepted():
    async with _client("s3cret") as c:
        r = await c.get("/x", headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/incidents"),
        ("GET", "/signatures"),
        ("POST", "/diagnose"),
        ("POST", "/search"),
        ("POST", "/ingest/stream"),
        ("DELETE", "/documents/1"),
    ],
)
async def test_real_routes_are_protected(method, path):
    app = create_app(Settings(api_key="s3cret"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        assert (await c.request(method, path)).status_code == 401
