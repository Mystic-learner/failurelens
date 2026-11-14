import httpx

from app.core.config import Settings
from app.main import create_app
from shipper.sender import IngestClient


async def test_ui_auth_js_is_open_and_sends_key():
    app = create_app(Settings(api_key="s3cret"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/ui/auth.js")
    assert r.status_code == 200
    assert "X-API-Key" in r.text
    assert "javascript" in r.headers["content-type"]


async def test_shipper_key_from_env(monkeypatch):
    monkeypatch.setenv("FAILURELENS_API_KEY", "k1")
    client = IngestClient("http://t", "svc", "src")
    try:
        assert client._http.headers["x-api-key"] == "k1"
    finally:
        await client.aclose()


async def test_shipper_explicit_key_wins(monkeypatch):
    monkeypatch.setenv("FAILURELENS_API_KEY", "k1")
    client = IngestClient("http://t", "svc", "src", api_key="k2")
    try:
        assert client._http.headers["x-api-key"] == "k2"
    finally:
        await client.aclose()


async def test_shipper_no_key_no_header(monkeypatch):
    monkeypatch.delenv("FAILURELENS_API_KEY", raising=False)
    client = IngestClient("http://t", "svc", "src")
    try:
        assert "x-api-key" not in client._http.headers
    finally:
        await client.aclose()
