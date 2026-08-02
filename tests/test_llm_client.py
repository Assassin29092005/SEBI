"""Offline tests for the provider-agnostic LLM client (httpx.MockTransport, no network)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import app.llm.client as llm_client
import httpx
import pytest
from app.config import settings
from app.llm.client import (
    GeminiProvider,
    GroqProvider,
    LLMResponse,
    LLMUnavailable,
    get_provider,
    grounded_complete,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _gemini_body(text: str, *, usage: dict[str, int] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    if usage is not None:
        body["usageMetadata"] = usage
    return body


def _groq_body(text: str, *, usage: dict[str, int] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    if usage is not None:
        body["usage"] = usage
    return body


def _mock_client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "")


# ---------------------------------------------------------------- Gemini


def test_gemini_request_shape_and_response_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-2.0-flash")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_gemini_body("draft section text"))

    async def run() -> LLMResponse:
        async with _mock_client(handler) as http:
            return await GeminiProvider(http_client=http).complete(
                system="You are a DRHP drafter.", user="Draft the section.", temperature=0.0
            )

    result = asyncio.run(run())

    request = captured[0]
    assert request.method == "POST"
    assert request.url.host == "generativelanguage.googleapis.com"
    assert request.url.path == "/v1beta/models/gemini-2.0-flash:generateContent"
    # The key travels as a header, not a URL query param — see GeminiProvider.complete.
    assert "key" not in request.url.params
    assert request.headers["x-goog-api-key"] == "test-gemini-key"
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "You are a DRHP drafter."}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Draft the section."}]}]
    assert body["generationConfig"]["temperature"] == 0.0
    assert result.text == "draft section text"
    assert result.provider == "gemini"
    assert result.model == "gemini-2.0-flash"


# ---------------------------------------------------------------- Groq


def test_groq_request_shape_and_response_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "groq_api_key", "test-groq-key")
    monkeypatch.setattr(settings, "groq_model", "llama-3.3-70b-versatile")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_groq_body("validated section text"))

    async def run() -> LLMResponse:
        async with _mock_client(handler) as http:
            return await GroqProvider(http_client=http).complete(
                system="You are a validator.", user="Check the section.", temperature=0.0
            )

    result = asyncio.run(run())

    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-groq-key"
    body = json.loads(request.content)
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["messages"] == [
        {"role": "system", "content": "You are a validator."},
        {"role": "user", "content": "Check the section."},
    ]
    assert body["temperature"] == 0.0
    assert result.text == "validated section text"
    assert result.provider == "groq"
    assert result.model == "llama-3.3-70b-versatile"


# ---------------------------------------------------------------- retry behaviour


def test_retries_once_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm_client, "_RETRY_BACKOFF_SECONDS", 0.0)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=_gemini_body("after retry"))

    async def run() -> LLMResponse:
        async with _mock_client(handler) as http:
            return await GeminiProvider(http_client=http).complete(
                system="s", user="u", temperature=0.0
            )

    result = asyncio.run(run())
    assert len(calls) == 2
    assert result.text == "after retry"


def test_gives_up_after_one_retry_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(llm_client, "_RETRY_BACKOFF_SECONDS", 0.0)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, json={"error": "down"})

    async def run() -> LLMResponse:
        async with _mock_client(handler) as http:
            return await GroqProvider(http_client=http).complete(
                system="s", user="u", temperature=0.0
            )

    with pytest.raises(LLMUnavailable):
        asyncio.run(run())
    assert len(calls) == 2  # original attempt + exactly one retry


def test_no_retry_on_plain_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "bad-key")
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": "bad request"})

    async def run() -> LLMResponse:
        async with _mock_client(handler) as http:
            return await GeminiProvider(http_client=http).complete(
                system="s", user="u", temperature=0.0
            )

    with pytest.raises(LLMUnavailable):
        asyncio.run(run())
    assert len(calls) == 1


# ---------------------------------------------------------------- provider selection


def test_get_provider_raises_llm_unavailable_without_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_keys(monkeypatch)
    with pytest.raises(LLMUnavailable):
        get_provider()


def test_grounded_complete_raises_llm_unavailable_without_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_keys(monkeypatch)
    with pytest.raises(LLMUnavailable):
        asyncio.run(
            grounded_complete(system="s", user="u", context_fact_ids=["fact.one"], feature="test")
        )


def test_get_provider_respects_llm_provider_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "g-key")
    monkeypatch.setattr(settings, "groq_api_key", "q-key")
    monkeypatch.setattr(settings, "llm_provider", "groq")
    assert isinstance(get_provider(), GroqProvider)
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    assert isinstance(get_provider(), GeminiProvider)


def test_get_provider_falls_back_to_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "q-key")
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    assert isinstance(get_provider(), GroqProvider)


# ---------------------------------------------------------------- entry point


def test_grounded_complete_routes_through_selected_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "q-key")
    monkeypatch.setattr(settings, "llm_provider", "groq")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_groq_body("grounded output"))

    async def run() -> LLMResponse:
        async with _mock_client(handler) as http:
            return await grounded_complete(
                system="s",
                user="u",
                context_fact_ids=["issuer.name", "issue.size"],
                feature="test",
                http_client=http,
            )

    result = asyncio.run(run())
    assert result.text == "grounded output"
    assert result.provider == "groq"


# ---------------------------------------------------------- token usage / cost tracking


def test_gemini_provider_extracts_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_gemini_body(
                "text", usage={"promptTokenCount": 123, "candidatesTokenCount": 45}
            ),
        )

    async def run() -> LLMResponse:
        async with _mock_client(handler) as http:
            return await GeminiProvider(http_client=http).complete(
                system="s", user="u", temperature=0.0
            )

    result = asyncio.run(run())
    assert result.input_tokens == 123
    assert result.output_tokens == 45


def test_groq_provider_extracts_token_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_groq_body("text", usage={"prompt_tokens": 88, "completion_tokens": 12})
        )

    async def run() -> LLMResponse:
        async with _mock_client(handler) as http:
            return await GroqProvider(http_client=http).complete(
                system="s", user="u", temperature=0.0
            )

    result = asyncio.run(run())
    assert result.input_tokens == 88
    assert result.output_tokens == 12


def test_provider_response_without_usage_field_leaves_tokens_none() -> None:
    async def run() -> LLMResponse:
        async with _mock_client(
            lambda request: httpx.Response(200, json=_gemini_body("text"))
        ) as http:
            return await GeminiProvider(http_client=http).complete(
                system="s", user="u", temperature=0.0
            )

    result = asyncio.run(run())
    assert result.input_tokens is None
    assert result.output_tokens is None


def test_grounded_complete_records_a_usage_event_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.llm_usage import get_llm_usage_log

    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "groq_api_key", "q-key")
    monkeypatch.setattr(settings, "llm_provider", "groq")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_groq_body("out", usage={"prompt_tokens": 10, "completion_tokens": 5})
        )

    async def run() -> LLMResponse:
        async with _mock_client(handler) as http:
            return await grounded_complete(
                system="s",
                user="u",
                context_fact_ids=[],
                feature="my_feature",
                http_client=http,
            )

    asyncio.run(run())

    events = get_llm_usage_log().list_events()
    assert len(events) == 1
    assert events[0].feature == "my_feature"
    assert events[0].provider == "groq"
    assert events[0].input_tokens == 10
    assert events[0].output_tokens == 5


def test_grounded_complete_records_nothing_when_provider_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.llm_usage import get_llm_usage_log

    _clear_keys(monkeypatch)
    with pytest.raises(LLMUnavailable):
        asyncio.run(
            grounded_complete(system="s", user="u", context_fact_ids=[], feature="my_feature")
        )
    assert get_llm_usage_log().list_events() == []
