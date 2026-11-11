"""LLM integration module.

Talks to any OpenAI-compatible chat-completions endpoint (OpenRouter by default) and
handles the failure modes a free API tier actually produces:

* 429 rate limits   -> honour Retry-After, back off, then switch to a fallback model
* 5xx / timeouts    -> exponential backoff with jitter
* bad JSON mode     -> retry once without JSON mode (the parser can still recover)
* 401/403           -> fail fast with a clear message (retrying won't help)
* daily quota used  -> fail fast; the fallback model shares the same quota
* 404 model gone    -> switch to the fallback model
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

import httpx

from .config import Settings


class LLMError(Exception):
    """An LLM failure with a message that is safe to show the user."""

    def __init__(self, message: str, *, kind: str = "error", retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.retryable = retryable


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 1
    notices: list[str] = field(default_factory=list)


class ChatModel(Protocol):
    name: str

    async def chat(self, messages: list[dict]) -> LLMResult: ...


def parse_wait(value: str | None) -> float | None:
    """Parse wait hints like '2', '2.5', '7.66s', '1m30s' or '120ms' into seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    total, matched = 0.0, False
    for number, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value):
        matched = True
        n = float(number)
        total += {"ms": n / 1000, "s": n, "m": n * 60, "h": n * 3600}[unit]
    return total if matched else None


def _error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or err)
        return str(err)
    except Exception:
        return resp.text[:300]


class ChatClient:
    """OpenAI-compatible chat client with retries, backoff and model fallback."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.s = settings
        self.name = settings.model
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.llm_timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                # Optional OpenRouter attribution headers; other providers ignore them.
                "HTTP-Referer": "https://github.com/joudiheilani/CS4680_A4",
                "X-Title": "StudyPilot",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _backoff(self, attempt: int) -> float:
        return min(self.s.llm_max_wait, 2**attempt) + random.uniform(0, 0.5)

    async def chat(self, messages: list[dict]) -> LLMResult:
        models = [self.s.model]
        if self.s.fallback_model and self.s.fallback_model != self.s.model:
            models.append(self.s.fallback_model)

        notices: list[str] = []
        attempts = 0
        last_error: LLMError | None = None

        for index, model in enumerate(models):
            if index > 0:
                notices.append(f"Switching to fallback model {model}.")
            json_mode = True
            attempt = 0
            while attempt <= self.s.llm_max_retries:
                attempts += 1
                payload: dict = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 1024,
                }
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}

                started = time.monotonic()
                try:
                    resp = await self._client.post("/chat/completions", json=payload)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = LLMError(
                        f"Couldn't reach the language model ({type(exc).__name__}).",
                        kind="network",
                        retryable=True,
                    )
                    if attempt < self.s.llm_max_retries:
                        wait = self._backoff(attempt)
                        notices.append(f"Network error, retrying in {wait:.0f}s.")
                        await self._sleep(wait)
                        attempt += 1
                        continue
                    break

                latency_ms = int((time.monotonic() - started) * 1000)
                status = resp.status_code

                if status == 200:
                    try:
                        data = resp.json()
                        text = data["choices"][0]["message"]["content"] or ""
                    except (ValueError, KeyError, IndexError, TypeError):
                        last_error = LLMError("The model sent an unreadable response.", kind="bad_response")
                        attempt += 1
                        continue
                    usage = data.get("usage") or {}
                    return LLMResult(
                        text=text,
                        model=model,
                        prompt_tokens=int(usage.get("prompt_tokens") or 0),
                        completion_tokens=int(usage.get("completion_tokens") or 0),
                        latency_ms=latency_ms,
                        attempts=attempts,
                        notices=notices,
                    )

                detail = _error_detail(resp)

                if status in (401, 403):
                    raise LLMError(
                        "The LLM API key was rejected. Check OPENROUTER_API_KEY on the server.", kind="auth"
                    )

                if status == 400 and json_mode and "json" in detail.lower():
                    # Some providers reject replies that fail their JSON validator; our parser is more forgiving.
                    json_mode = False
                    notices.append("Model produced malformed JSON; retrying without JSON mode.")
                    continue

                if status == 404 or (status == 400 and "model" in detail.lower() and "decommission" in detail.lower()):
                    last_error = LLMError(f"Model {model} is unavailable.", kind="model_unavailable")
                    break

                if status == 413:
                    raise LLMError(
                        "That request was too large for the free model. Try a shorter message or reset the chat.",
                        kind="too_large",
                    )

                if status == 402:
                    raise LLMError(
                        "The LLM account is out of credits. Free models need a balance of at least $0.",
                        kind="quota",
                    )

                if status == 429 and "per-day" in detail.lower():
                    raise LLMError(
                        "Today's free LLM quota is used up (it resets at midnight UTC). "
                        "The app still works in demo mode if you restart it without a key.",
                        kind="quota",
                    )

                if status == 429:
                    last_error = LLMError(
                        "The free LLM tier is rate limited right now. Please wait a minute and try again.",
                        kind="rate_limit",
                        retryable=True,
                    )
                    wait = parse_wait(resp.headers.get("retry-after"))
                    if wait is None:
                        wait = self._backoff(attempt)
                    if wait > self.s.llm_max_wait or attempt >= self.s.llm_max_retries:
                        break  # don't make the user wait forever: try the fallback model
                    notices.append(f"Rate limited by {model}; waiting {wait:.0f}s before retrying.")
                    await self._sleep(wait)
                    attempt += 1
                    continue

                if status >= 500:
                    last_error = LLMError(
                        f"The LLM provider had a server error ({status}).", kind="server", retryable=True
                    )
                    if attempt < self.s.llm_max_retries:
                        wait = self._backoff(attempt)
                        notices.append(f"Provider error {status}, retrying in {wait:.0f}s.")
                        await self._sleep(wait)
                        attempt += 1
                        continue
                    break

                raise LLMError(f"LLM request failed ({status}): {detail[:200]}", kind="bad_request")

        raise last_error or LLMError("The language model is unavailable right now.")
