import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.llm import ChatClient, LLMError, parse_wait


def ok(content="{}", model="m"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 5}})


def client(handler, **overrides):
    s = Settings(api_key="test", model="primary", fallback_model="backup", **overrides)
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    return ChatClient(s, transport=httpx.MockTransport(handler), sleep=fake_sleep), slept


def test_parse_wait():
    assert parse_wait("2") == 2
    assert parse_wait("7.5s") == 7.5
    assert parse_wait("1m30s") == 90
    assert parse_wait("120ms") == pytest.approx(0.12)
    assert parse_wait("soon") is None


def test_retries_after_rate_limit():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "slow down"}})
        return ok('{"reply": "hi"}')

    c, slept = client(handler)
    res = asyncio.run(c.chat([{"role": "user", "content": "x"}]))
    assert res.text == '{"reply": "hi"}' and res.attempts == 2
    assert slept == [3.0]
    assert any("Rate limited" in n for n in res.notices)


def test_long_rate_limit_switches_to_fallback():
    models = []

    def handler(request):
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == "primary":
            return httpx.Response(429, headers={"retry-after": "120"})
        return ok()

    c, slept = client(handler)
    res = asyncio.run(c.chat([]))
    assert res.model == "backup" and models == ["primary", "backup"] and slept == []


def test_auth_error_fails_fast():
    c, _ = client(lambda r: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(LLMError) as exc:
        asyncio.run(c.chat([]))
    assert exc.value.kind == "auth"


def test_server_errors_retry_then_fail():
    count = []

    def handler(request):
        count.append(1)
        return httpx.Response(503)

    c, slept = client(handler)
    with pytest.raises(LLMError) as exc:
        asyncio.run(c.chat([]))
    assert exc.value.kind == "server"
    assert len(count) == 8  # 4 attempts on each of the two models
    assert len(slept) == 6


def test_json_mode_failure_retries_without_json_mode():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "Failed to generate JSON", "code": "json_validate_failed"}})
        return ok("text {}")

    c, _ = client(handler)
    res = asyncio.run(c.chat([]))
    assert res.text == "text {}" and "response_format" not in bodies[-1]
