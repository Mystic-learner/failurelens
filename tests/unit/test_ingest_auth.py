"""Machine-facing ingest routes require the key; browser upload stays public."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.ingest import router as ingest_router
from app.api.ingest_status import router as status_router
from app.core.config import Settings


def _client(api_key, ingest_api_key=None):
    app = FastAPI()
    app.state.settings = Settings(api_key=api_key, ingest_api_key=ingest_api_key)
    app.include_router(ingest_router)
    app.include_router(status_router)
    return TestClient(app)


def test_stream_rejects_without_key():
    r = _client(api_key="s3cret").post("/ingest/stream", content=b"line")
    assert r.status_code in (401, 403)


def test_stream_accepts_api_key_fallback():
    r = _client(api_key="s3cret").post("/ingest/stream", content=b"line", headers={"X-API-Key": "s3cret"})
    assert r.status_code != 401


def test_stream_prefers_ingest_key():
    c = _client(api_key="s3cret", ingest_api_key="ingest-only")
    assert c.post("/ingest/stream", content=b"line", headers={"X-API-Key": "s3cret"}).status_code in (401, 403)
    assert c.post("/ingest/stream", content=b"line", headers={"X-API-Key": "ingest-only"}).status_code != 401


def test_committed_requires_key():
    c = _client(api_key="s3cret")
    assert c.post("/ingest/committed", json={"keys": []}).status_code in (401, 403)
    assert c.post("/ingest/committed", json={"keys": []}, headers={"X-API-Key": "s3cret"}).status_code != 401
