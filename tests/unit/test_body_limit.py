from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.types import Message, Receive, Scope, Send

from app.core.middleware import BodyLimitMiddleware, BodyTooLarge
from app.core.request_id import RequestIdMiddleware

LIMIT = 100


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(BodyLimitMiddleware, max_bytes=LIMIT)
    app.add_middleware(RequestIdMiddleware)

    @app.post("/echo-size")
    async def echo_size(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    return app


async def post(**kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/echo-size", **kwargs)  # type: ignore[arg-type]


async def test_body_at_the_limit_is_accepted() -> None:
    response = await post(content=b"x" * LIMIT)
    assert response.status_code == 200
    assert response.json() == {"size": LIMIT}


async def test_empty_body_is_accepted() -> None:
    assert (await post(content=b"")).json() == {"size": 0}


async def test_declared_oversize_is_rejected_with_json_error() -> None:
    response = await post(content=b"x" * (LIMIT + 1))
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "payload_too_large"
    assert error["request_id"] == response.headers["x-request-id"]


async def test_streamed_oversize_without_content_length_is_rejected() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(5):
            yield b"x" * 30  # 150 bytes total, no Content-Length header

    response = await post(content=chunks())
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_streamed_body_under_the_limit_is_accepted() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(3):
            yield b"x" * 30

    assert (await post(content=chunks())).json() == {"size": 90}


async def test_oversize_after_response_started_propagates() -> None:
    """If the app already began its response, the status can no longer change: re-raise."""

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.body", "body": b"ok"})

    messages: list[Message] = [
        {"type": "http.request", "body": b"x" * 60, "more_body": True},
        {"type": "http.request", "body": b"x" * 60, "more_body": False},
    ]

    async def receive() -> Message:
        return messages.pop(0)

    async def send(message: Message) -> None:
        return None

    scope: Scope = {"type": "http", "headers": [], "state": {}}
    with pytest.raises(BodyTooLarge):
        await BodyLimitMiddleware(app, max_bytes=LIMIT)(scope, receive, send)


async def test_non_http_scopes_pass_through() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        return None

    await BodyLimitMiddleware(app, max_bytes=1)({"type": "lifespan"}, receive, send)
    assert seen == ["lifespan"]
