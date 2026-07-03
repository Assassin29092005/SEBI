"""Single provider-agnostic LLM client (free tiers: Gemini / Groq).

Conventions enforced here, not at call sites:
- temperature 0 for generation and validation;
- every call logs the fact IDs present in its prompt context so citations
  are reconstructible.
"""

from __future__ import annotations

import logging
from typing import Protocol

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger("drhp.llm")


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str


class LLMProvider(Protocol):
    async def complete(self, system: str, user: str, temperature: float) -> LLMResponse: ...


class GeminiProvider:
    async def complete(self, system: str, user: str, temperature: float) -> LLMResponse:
        # TODO: httpx call to the Gemini API using settings.gemini_api_key
        raise NotImplementedError("Gemini provider: implement with first extraction feature")


class GroqProvider:
    async def complete(self, system: str, user: str, temperature: float) -> LLMResponse:
        # TODO: httpx call to the Groq API using settings.groq_api_key
        raise NotImplementedError("Groq provider: implement with first extraction feature")


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
