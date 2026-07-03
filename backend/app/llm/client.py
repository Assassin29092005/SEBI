"""Single provider-agnostic LLM client (free tiers: Gemini / Groq).

Conventions enforced here, not at call sites:
- temperature 0 for generation and validation;
- every call logs the fact IDs present in its prompt context so citations
  are reconstructible.

Offline-first: when no API key is configured (or the API is unreachable),
``LLMUnavailable`` is raised so callers can switch to their deterministic
fallback path. The demo must run without any keys.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger("drhp.llm")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"

_TIMEOUT_SECONDS = 30.0
# Module-level so tests can patch it to 0; one retry only (hackathon, not production).
_RETRY_BACKOFF_SECONDS = 0.5


class LLMUnavailable(Exception):
    """No LLM provider is usable: no API key configured, or the API call failed.

    Callers catch this to switch to their deterministic (non-LLM) fallback path —
    every LLM-dependent feature must have one so the demo runs offline.
    """


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str


class LLMProvider(Protocol):
    async def complete(self, system: str, user: str, temperature: float) -> LLMResponse: ...


def _is_retryable(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


async def _post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """POST with a single retry on 429/5xx after a short backoff.

    Any persistent HTTP failure (status or transport) is normalised to
    ``LLMUnavailable`` so callers have exactly one exception to catch.
    """
    try:
        response = await client.post(url, json=payload, headers=headers)
        if _is_retryable(response.status_code):
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
            response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        raise LLMUnavailable(
            f"LLM API returned HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"LLM API request failed: {exc}") from exc


async def _request(
    http_client: httpx.AsyncClient | None,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Use the injected client if given (tests), else a short-lived one per call."""
    if http_client is not None:
        return await _post_with_retry(http_client, url, payload, headers)
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        return await _post_with_retry(client, url, payload, headers)


class GeminiProvider:
    """Google Gemini via the generativelanguage REST API (generateContent)."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    async def complete(self, system: str, user: str, temperature: float) -> LLMResponse:
        url = (
            f"{GEMINI_BASE_URL}/models/{settings.gemini_model}:generateContent"
            f"?key={settings.gemini_api_key}"
        )
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature},
        }
        response = await _request(self._http_client, url, payload)
        data = response.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable(f"Unexpected Gemini response shape: {exc!r}") from exc
        return LLMResponse(text=text, provider="gemini", model=settings.gemini_model)


class GroqProvider:
    """Groq via its OpenAI-compatible chat completions API."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    async def complete(self, system: str, user: str, temperature: float) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": settings.groq_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
        response = await _request(self._http_client, GROQ_CHAT_COMPLETIONS_URL, payload, headers)
        data = response.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable(f"Unexpected Groq response shape: {exc!r}") from exc
        return LLMResponse(text=text, provider="groq", model=settings.groq_model)


def get_provider(http_client: httpx.AsyncClient | None = None) -> LLMProvider:
    """Pick the preferred provider (``settings.llm_provider``) if its key is set,
    else fall back to the other configured provider.

    Raises ``LLMUnavailable`` when no provider has an API key — callers use this
    to switch to their deterministic fallback.
    """
    order = ("groq", "gemini") if settings.llm_provider == "groq" else ("gemini", "groq")
    for name in order:
        if name == "gemini" and settings.gemini_api_key:
            return GeminiProvider(http_client=http_client)
        if name == "groq" and settings.groq_api_key:
            return GroqProvider(http_client=http_client)
    raise LLMUnavailable(
        "No LLM API key configured (set GEMINI_API_KEY or GROQ_API_KEY in .env); "
        "use the deterministic fallback path."
    )


async def grounded_complete(
    system: str,
    user: str,
    context_fact_ids: list[str],
    temperature: float = 0.0,
    http_client: httpx.AsyncClient | None = None,
) -> LLMResponse:
    """The only entry point generation/validation code should use."""
    logger.info("llm_call fact_ids=%s temperature=%s", context_fact_ids, temperature)
    provider = get_provider(http_client=http_client)
    return await provider.complete(system=system, user=user, temperature=temperature)
