"""Sarvam AI translation client — Indic-first translation for draft review.

Deliberately *not* an ``app.llm.client.LLMProvider``. That Protocol is a chat
interface (system prompt + user prompt → completion) and every other
LLM-touching feature in this app needs it: grounded generation, extraction,
the adversarial examiner. Sarvam's translation endpoint is a different shape
— ``input`` in, ``translated_text`` out, no prompting — and pretending
otherwise would mean inventing a fake system prompt the API ignores. So this
is a separate, narrow client used by one caller (``app.generate.translate``).

Why a second provider at all: Gemini/Groq translate Indian languages as a
side capability; Sarvam builds for them specifically. The five other LLM
call sites keep using the general provider, which is the right tool for long
English reasoning prompts.

Two API details drive the code below:

* **Length limit.** 1000 characters for ``mayura:v1``, 2000 for
  ``sarvam-translate:v1``. Real generated sections in this app run to a
  median of ~865 characters and a maximum of ~2066, so a dozen sections
  already exceed the smaller limit and one exceeds the larger. Chunking is
  required, not a precaution — and it splits on paragraph then sentence
  boundaries, never mid-number, because a digit sliced across two requests
  would fail the caller's number-preservation guard and silently discard an
  otherwise good translation.

* **``numerals_format``.** Left to default, Sarvam may render numbers in
  Devanagari numerals. ``app.generate.translate`` requires every digit
  sequence in the output to appear verbatim in the English original, so
  native numerals would fail that guard on every section — the promoter
  would see English and never learn why. ``international`` keeps ₹12,50,00,000
  as ASCII digits.

Offline-first like every other integration here: no key configured, or any
failure, raises ``LLMUnavailable`` and the caller falls back.
"""

from __future__ import annotations

import logging
import re

import httpx

from app.config import settings
from app.llm.client import LLMUnavailable

logger = logging.getLogger("drhp.llm.sarvam")

SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"

_TIMEOUT_SECONDS = 30.0

# Documented maxima per model. Kept a little under the real limit: the API
# counts characters its own way and a rejected chunk costs a whole section's
# translation, so the margin is cheaper than the retry.
_MODEL_LIMITS: dict[str, int] = {"mayura:v1": 1000, "sarvam-translate:v1": 2000}
_LIMIT_MARGIN = 50

# Our language codes (shared with the wizard) → Sarvam's BCP-47-ish codes.
_LANGUAGE_CODES: dict[str, str] = {
    "hi": "hi-IN",
    "bn": "bn-IN",
    "gu": "gu-IN",
    "kn": "kn-IN",
    "ml": "ml-IN",
    "mr": "mr-IN",
    "od": "od-IN",
    "pa": "pa-IN",
    "ta": "ta-IN",
    "te": "te-IN",
}

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
# Sentence end followed by whitespace. Requires the terminator to be preceded
# by a non-digit so "12.50 crore" is never treated as a sentence boundary —
# splitting there would cut a number in half.
_SENTENCE_SPLIT = re.compile(r"(?<=[^\d][.!?])\s+")


def is_available() -> bool:
    """True when a Sarvam key is configured. No network call."""
    return bool(settings.sarvam_api_key)


def _chunk_limit() -> int:
    model = settings.sarvam_model
    return _MODEL_LIMITS.get(model, 1000) - _LIMIT_MARGIN


def split_for_translation(text: str, limit: int) -> list[str]:
    """Split ``text`` into pieces no longer than ``limit`` characters.

    Paragraph boundaries first, then sentence boundaries within a paragraph
    that is still too long. A single sentence longer than the limit is passed
    through whole rather than chopped mid-clause: the API will reject it and
    the caller falls back to English, which is far better than returning half
    a sentence — or a number split across two requests — as if it were a
    complete translation.
    """
    if not text.strip():
        return []

    pieces: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT.split(text):
        if not paragraph.strip():
            continue
        if len(paragraph) <= limit:
            pieces.append(paragraph)
            continue
        # Too long — pack whole sentences up to the limit.
        buffer = ""
        for sentence in _SENTENCE_SPLIT.split(paragraph):
            if not sentence:
                continue
            candidate = f"{buffer} {sentence}".strip() if buffer else sentence
            if len(candidate) <= limit:
                buffer = candidate
            else:
                if buffer:
                    pieces.append(buffer)
                buffer = sentence
        if buffer:
            pieces.append(buffer)
    return pieces


async def _translate_chunk(
    client: httpx.AsyncClient, chunk: str, target_code: str
) -> str:
    response = await client.post(
        SARVAM_TRANSLATE_URL,
        headers={"api-subscription-key": settings.sarvam_api_key},
        json={
            "input": chunk,
            "source_language_code": "en-IN",
            "target_language_code": target_code,
            "model": settings.sarvam_model,
            # Regulatory prose, not conversation.
            "mode": "formal",
            # Non-negotiable — see the module docstring.
            "numerals_format": "international",
        },
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return str(response.json()["translated_text"])


async def translate(text: str, lang: str) -> str:
    """Translate English ``text`` into ``lang``.

    Raises ``LLMUnavailable`` when no key is configured, the language is not
    one Sarvam supports, or any request fails — the caller treats all three
    the same way, by showing the English original.
    """
    if not settings.sarvam_api_key:
        raise LLMUnavailable("SARVAM_API_KEY is not set")

    target_code = _LANGUAGE_CODES.get(lang)
    if target_code is None:
        raise LLMUnavailable(f"Sarvam has no language code mapped for {lang!r}")

    chunks = split_for_translation(text, _chunk_limit())
    if not chunks:
        return ""

    try:
        async with httpx.AsyncClient() as client:
            translated = [await _translate_chunk(client, c, target_code) for c in chunks]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        # KeyError/ValueError cover a 200 whose body isn't the documented
        # shape — an API change should degrade to English, not 500.
        logger.info("Sarvam translation unavailable: %s", exc)
        raise LLMUnavailable(f"Sarvam translation failed: {exc}") from exc

    # Rejoin on blank lines: chunks came from paragraph/sentence boundaries,
    # so this restores readable structure rather than one run-on block.
    return "\n\n".join(t.strip() for t in translated if t.strip())
