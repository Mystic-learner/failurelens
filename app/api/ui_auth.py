"""Serves the small script that makes the web UI send the API key."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()

_JS = '''(function () {
  // No key prompt. Same-origin requests are allowed by the server via Origin/sec-fetch-site check.
  // External clients (shipper) must send X-API-Key themselves.
})();
'''


@router.get("/ui/auth.js", include_in_schema=False)
async def ui_auth_js() -> Response:
    return Response(_JS, media_type="application/javascript")
