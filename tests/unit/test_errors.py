import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.core.errors import error_body, register_exception_handlers
from app.core.request_id import RequestIdMiddleware


class Payload(BaseModel):
    count: int


def build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    @app.post("/validate")
    async def validate(payload: Payload) -> dict[str, int]:
        return {"count": payload.count}

    @app.get("/limited")
    async def limited() -> None:
        raise HTTPException(429, "queue full", headers={"Retry-After": "2"})

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret-internal-detail")

    return app


def client_for(app: FastAPI, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


def test_error_body_shape() -> None:
    assert error_body("c", "m", "r") == {"error": {"code": "c", "message": "m", "request_id": "r"}}


async def test_404_uses_the_error_shape() -> None:
    async with client_for(build_app()) as client:
        response = await client.get("/missing")
    error = response.json()["error"]
    assert response.status_code == 404
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["x-request-id"]


async def test_405_uses_the_error_shape() -> None:
    async with client_for(build_app()) as client:
        response = await client.get("/validate")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_http_exception_keeps_its_headers() -> None:
    async with client_for(build_app()) as client:
        response = await client.get("/limited")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "2"
    assert response.json()["error"]["code"] == "too_many_requests"


async def test_validation_error_is_422_and_does_not_echo_input() -> None:
    async with client_for(build_app()) as client:
        response = await client.post("/validate", json={"count": "password=hunter2"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "count" in error["message"]
    assert "hunter2" not in response.text


async def test_unhandled_error_is_500_without_leaking_details() -> None:
    async with client_for(build_app(), raise_app_exceptions=False) as client:
        response = await client.get("/boom")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert "secret-internal-detail" not in response.text
    assert error["request_id"] != "-"
