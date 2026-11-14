import httpx
import pytest

from app.services.diagnosis import (
    FakeLLM,
    LLMResponse,
    OpenAILLM,
    TransientLLMError,
    _call_with_retry,
    _Usage,
)


async def _run(script):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    out = await _call_with_retry(FakeLLM(script), "sys", "user", _Usage(), fake_sleep, 0.5)
    return out, sleeps


async def test_429_waits_for_retry_after():
    out, sleeps = await _run([TransientLLMError("http 429", retry_after_s=7.0), LLMResponse("ok")])
    assert out == "ok"
    assert sleeps == [7.0]


async def test_429_wait_is_capped():
    _, sleeps = await _run([TransientLLMError("http 429", retry_after_s=300.0), LLMResponse("ok")])
    assert sleeps == [20.0]


async def test_other_transient_uses_short_backoff():
    _, sleeps = await _run([TransientLLMError("http 503"), LLMResponse("ok")])
    assert sleeps == [0.5]


async def test_second_failure_still_raises():
    with pytest.raises(TransientLLMError):
        await _run([TransientLLMError("a", retry_after_s=1.0), TransientLLMError("b")])


def _llm(headers):
    def handler(request):
        return httpx.Response(429, headers=headers)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAILLM("k", client=client)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Retry-After": "3"}, 3.0),
        ({}, 5.0),
        ({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 5.0),
        ({"Retry-After": "-4"}, 5.0),
    ],
)
async def test_http_429_sets_retry_after(headers, expected):
    with pytest.raises(TransientLLMError) as info:
        await _llm(headers).complete("s", "u")
    assert info.value.retry_after_s == expected
