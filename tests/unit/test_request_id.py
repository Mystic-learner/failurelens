import httpx
from fastapi import FastAPI, Request

from app.core.request_id import RequestIdMiddleware, request_id_var


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/id")
    async def read_id(request: Request) -> dict[str, str]:
        return {"state": request.state.request_id, "contextvar": request_id_var.get()}

    return app


async def get(headers: dict[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get("/id", headers=headers)


async def test_generates_an_id_and_exposes_it_everywhere() -> None:
    response = await get()
    rid = response.headers["x-request-id"]
    assert len(rid) == 32
    assert response.json() == {"state": rid, "contextvar": rid}


async def test_echoes_a_valid_client_id() -> None:
    response = await get({"X-Request-ID": "trace-abc.123_X"})
    assert response.headers["x-request-id"] == "trace-abc.123_X"
    assert response.json()["state"] == "trace-abc.123_X"


async def test_replaces_an_invalid_client_id() -> None:
    for bad in ("has spaces in it", "x" * 65, "semi;colon"):
        response = await get({"X-Request-ID": bad})
        assert response.headers["x-request-id"] != bad
        assert len(response.headers["x-request-id"]) == 32


async def test_contextvar_is_reset_after_the_request() -> None:
    await get()
    assert request_id_var.get() == "-"
