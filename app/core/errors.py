import logging
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "too_many_requests",
}


def error_body(code: str, message: str, request_id: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message, "request_id": request_id}}


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", "-"))


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    http_exc = cast(StarletteHTTPException, exc)
    code = _CODES.get(http_exc.status_code, f"http_{http_exc.status_code}")
    return JSONResponse(
        error_body(code, str(http_exc.detail), request_id_of(request)),
        status_code=http_exc.status_code,
        headers=http_exc.headers,
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    validation_exc = cast(RequestValidationError, exc)
    # Only location and message are reported. The rejected input is never echoed back,
    # because it may contain raw log content.
    parts = [
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in validation_exc.errors()
    ]
    return JSONResponse(
        error_body("validation_error", "; ".join(parts), request_id_of(request)),
        status_code=422,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return JSONResponse(
        error_body("internal_error", "Internal server error", request_id_of(request)),
        status_code=500,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
