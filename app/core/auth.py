"""Optional shared-secret API key check. Disabled when settings.api_key is empty."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request


async def require_api_key(request: Request) -> None:
    expected = request.app.state.settings.api_key
    if not expected:
        return
    supplied = request.headers.get("x-api-key")
    if supplied is None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    if supplied is None or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_ingest_key(request: Request) -> None:
    """Protect machine-facing ingest routes. Uses ingest_api_key, falls back to api_key.
    Same-origin browser requests (Break it / Try your log) are allowed without a key."""
    settings = getattr(request.app.state, "settings", None)
    expected = getattr(settings, "ingest_api_key", None) or getattr(settings, "api_key", None)
    if not expected:
        return
    # Allow same-origin browser requests (the demo UI)
    origin = request.headers.get("origin", "")
    referer = request.headers.get("referer", "")
    host = request.headers.get("host", "")
    sec_fetch_site = request.headers.get("sec-fetch-site", "")
    # Modern browsers send sec-fetch-site on all requests
    if sec_fetch_site in ("same-origin", "same-site", "none"):
        return
    if origin and host:
        origin_host = origin.replace("http://", "").replace("https://", "").split("/")[0]
        if origin_host == host:
            return
    if referer and host:
        referer_host = referer.replace("http://", "").replace("https://", "").split("/")[0]
        if referer_host == host:
            return
    supplied = request.headers.get("x-api-key")
    if supplied is None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    if supplied != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
