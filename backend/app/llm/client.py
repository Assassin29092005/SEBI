"""Single provider-agnostic LLM client (free tiers: Gemini / Groq).

Conventions enforced here, not at call sites:
- temperature 0 for generation and validation;
- every call logs the fact IDs present in its prompt context so citations
  are reconstructible.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger("drhp.llm")

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class LLMError(Exception):
    """Provider returned an error or an unusable response."""


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str


class LLMProvider(Protocol):
    async def complete(self, system: str, user: str, temperature: float) -> LLMResponse: ...


class GroqProvider:
    """Groq's OpenAI-compatible chat completions API."""

    _URL = "https://api.groq.com/openai/v1/chat/completions"

    async def complete(self, system: str, user: str, temperature: float) -> LLMResponse:
        if not settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not configured (see .env.example)")
        payload = {
            "model": settings.groq_model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                self._URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            )
        if resp.status_code != 200:
            raise LLMError(f"Groq API {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Groq API returned unexpected shape: {data}") from exc
        return LLMResponse(text=text, provider="groq", model=settings.groq_model)


class GeminiProvider:
    """Google Gemini generateContent API."""

    async def complete(self, system: str, user: str, temperature: float) -> LLMResponse:
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not configured (see .env.example)")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_model}:generateContent"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": temperature},
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"x-goog-api-key": settings.gemini_api_key},
            )
        if resp.status_code != 200:
            raise LLMError(f"Gemini API {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Gemini API returned unexpected shape: {data}") from exc
        return LLMResponse(text=text, provider="gemini", model=settings.gemini_model)


def get_provider() -> LLMProvider:
    if settings.llm_provider == "groq":
        return GroqProvider()
    return GeminiProvider()


async def grounded_complete(
    system: str,
    user: str,
    context_fact_ids: list[str],
    temperature: float = 0.0,
) -> LLMResponse:
    """The only entry point generation/validation code should use."""
    logger.info("llm_call fact_ids=%s temperature=%s", context_fact_ids, temperature)
    return await get_provider().complete(system=system, user=user, temperature=temperature)
