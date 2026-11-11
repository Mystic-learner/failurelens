import asyncio
from typing import Any

import httpx

from app.services.diagnosis import OpenAILLM

OK = {"choices": [{"message": {"content": "{}"}}], "usage": {}}


def _call(**kwargs: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["content"] = request.content
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=OK)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OpenAILLM("k-test", client=client, **kwargs)
    asyncio.run(llm.complete("sys", "usr"))
    return seen


def test_defaults_target_openai_with_strict_schema() -> None:
    seen = _call()
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["auth"] == "Bearer k-test"
    assert b'"json_schema"' in seen["content"]


def test_custom_base_url_and_json_object_mode() -> None:
    seen = _call(base_url="https://api.groq.com/openai/v1/", response_format="json_object")
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert b'"json_object"' in seen["content"]
    assert b'"json_schema"' not in seen["content"]
    assert b"JSON schema" in seen["content"]
