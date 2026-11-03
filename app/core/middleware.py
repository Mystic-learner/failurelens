from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_body


class BodyTooLarge(Exception):
    """Raised inside the app when a streamed body passes the limit."""


class BodyLimitMiddleware:
    """Rejects request bodies over max_bytes with 413, whether or not Content-Length is sent."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break  # malformed header: fall back to counting the bytes
                if declared > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return
                break

        received = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if response_started:
                raise  # too late to change the status code
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        request_id = str(scope.get("state", {}).get("request_id", "-"))
        response = JSONResponse(
            error_body(
                "payload_too_large",
                f"Request body exceeds {self.max_bytes} bytes",
                request_id,
            ),
            status_code=413,
        )
        await response(scope, receive, send)
