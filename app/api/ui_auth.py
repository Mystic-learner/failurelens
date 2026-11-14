"""Serves the small script that makes the web UI send the API key."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()

_JS = '''(function () {
  var KEY = "fl_api_key";
  function getKey() {
    try { return sessionStorage.getItem(KEY) || ""; } catch (e) { return ""; }
  }
  function setKey(v) {
    try { sessionStorage.setItem(KEY, v); } catch (e) { /* ignore */ }
  }
  var orig = window.fetch.bind(window);
  function withKey(init, key) {
    var o = Object.assign({}, init || {});
    var h = new Headers(o.headers || {});
    if (key) { h.set("X-API-Key", key); }
    o.headers = h;
    return o;
  }
  window.fetch = async function (input, init) {
    var local = typeof input === "string" && input.charAt(0) === "/" && input.charAt(1) !== "/";
    if (!local) { return orig(input, init); }
    var r = await orig(input, withKey(init, getKey()));
    if (r.status !== 401) { return r; }
    var k = window.prompt("API key required:");
    if (!k) { return r; }
    setKey(k);
    return orig(input, withKey(init, k));
  };
})();
'''


@router.get("/ui/auth.js", include_in_schema=False)
async def ui_auth_js() -> Response:
    return Response(_JS, media_type="application/javascript")
