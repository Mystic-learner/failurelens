import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.main import create_app


def _connect(api_key, url="/ws/events", headers=None):
    client = TestClient(create_app(Settings(api_key=api_key)))
    with client.websocket_connect(url, headers=headers or {}):
        pass


def _code(api_key, **kw):
    with pytest.raises(WebSocketDisconnect) as info:
        _connect(api_key, **kw)
    return info.value.code


def test_no_key_configured_passes_auth():
    assert _code(None) == 1013


def test_missing_key_rejected():
    assert _code("s3cret") == 1008


def test_wrong_key_rejected():
    assert _code("s3cret", url="/ws/events?api_key=nope") == 1008


def test_query_key_accepted():
    assert _code("s3cret", url="/ws/events?api_key=s3cret") == 1013


def test_header_key_accepted():
    assert _code("s3cret", headers={"x-api-key": "s3cret"}) == 1013


def test_bearer_accepted():
    assert _code("s3cret", headers={"authorization": "Bearer s3cret"}) == 1013
