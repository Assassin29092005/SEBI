"""Vernacular draft translation (app.generate.translate): LLM-only, no
deterministic fallback — the honest-degradation and number-preservation
guard are the whole point of this module.

Every scenario here was hand-verified against a standalone script before
being written up as a formal test.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from app.generate import translate as translate_mod
from app.generate.translate import translate_section_text
from app.llm.client import LLMResponse, LLMUnavailable


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _patch_llm(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    async def fake_grounded_complete(
        system: str,
        user: str,
        context_fact_ids: list[str],
        temperature: float = 0.0,
    ) -> LLMResponse:
        assert temperature == 0.0
        assert context_fact_ids == []
        return LLMResponse(text=text, provider="fake", model="fake")

    monkeypatch.setattr(translate_mod, "grounded_complete", fake_grounded_complete)


# --------------------------------------------------------------------------
# lang=en / unsupported lang: trivial no-op, no LLM call
# --------------------------------------------------------------------------


def test_lang_en_is_a_noop_and_never_calls_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_if_called(*args: object, **kwargs: object) -> LLMResponse:
        raise AssertionError("should never call the LLM for lang=en")

    monkeypatch.setattr(translate_mod, "grounded_complete", fail_if_called)
    result = run(translate_section_text("entry.a", "Issue size: Rs 14.00 crore.", "en"))
    assert result.lang == "en"
    assert result.translated is True
    assert result.text == "Issue size: Rs 14.00 crore."


def test_unsupported_language_code_falls_back_to_english_noop() -> None:
    result = run(translate_section_text("entry.a", "Issue size: Rs 14.00 crore.", "fr"))
    assert result.lang == "en"
    assert result.translated is True
    assert result.text == "Issue size: Rs 14.00 crore."


def test_empty_section_text_is_a_noop() -> None:
    result = run(translate_section_text("entry.a", "   ", "hi"))
    assert result.translated is True
    assert result.text == "   "


# --------------------------------------------------------------------------
# Offline (no LLM key / stub providers): honest fallback, never an error
# --------------------------------------------------------------------------


def test_offline_stub_providers_yield_honest_untranslated_fallback() -> None:
    # No monkeypatching: the real client's stub providers raise
    # NotImplementedError/LLMUnavailable in this offline test environment.
    result = run(translate_section_text("entry.a", "Issue size: Rs 14.00 crore.", "hi"))
    assert result.translated is False
    assert result.lang == "hi"
    assert result.text == "Issue size: Rs 14.00 crore."  # English original, not blank


def test_llm_unavailable_yields_fallback_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    async def raising_grounded_complete(
        system: str, user: str, context_fact_ids: list[str], temperature: float = 0.0
    ) -> LLMResponse:
        raise translate_mod.LLMUnavailable("no API key configured")

    monkeypatch.setattr(translate_mod, "grounded_complete", raising_grounded_complete)
    result = run(translate_section_text("entry.a", "Issue size: Rs 14.00 crore.", "hi"))
    assert result.translated is False
    assert result.text == "Issue size: Rs 14.00 crore."


# --------------------------------------------------------------------------
# LLM available: a valid translation is used; one that invents a number
# relative to the source is discarded in favour of the English original
# --------------------------------------------------------------------------


def test_llm_translation_preserving_numbers_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, "निर्गम आकार: Rs 14.00 crore.")
    result = run(translate_section_text("entry.a", "Issue size: Rs 14.00 crore.", "hi"))
    assert result.translated is True
    assert result.text == "निर्गम आकार: Rs 14.00 crore."
    assert result.lang == "hi"


def test_system_prompt_names_the_target_language(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_system: list[str] = []

    async def fake_grounded_complete(
        system: str, user: str, context_fact_ids: list[str], temperature: float = 0.0
    ) -> LLMResponse:
        seen_system.append(system)
        return LLMResponse(text="translated", provider="fake", model="fake")

    monkeypatch.setattr(translate_mod, "grounded_complete", fake_grounded_complete)
    run(translate_section_text("entry.a", "Some text with no digits.", "hi"))
    assert seen_system
    assert "Hindi" in seen_system[0]


def test_llm_inventing_a_number_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, "निर्गम आकार: Rs 99.00 crore.")  # 99 was never in the source
    result = run(translate_section_text("entry.a", "Issue size: Rs 14.00 crore.", "hi"))
    assert result.translated is False
    assert result.text == "Issue size: Rs 14.00 crore."


def test_llm_dropping_a_restated_number_is_fine() -> None:
    # The guard only forbids NEW numbers in the translation, not omitting
    # one — a translation that legitimately doesn't repeat every figure
    # (e.g. a summarising clause) isn't a hallucination just for that.
    original = "Issue size: Rs 14.00 crore, comprising a fresh issue and an OFS."
    assert translate_mod._preserves_numbers(original, "निर्गम विवरण।")  # no digits at all


def test_llm_empty_translation_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, "   ")
    result = run(translate_section_text("entry.a", "Issue size: Rs 14.00 crore.", "hi"))
    assert result.translated is False
    assert result.text == "Issue size: Rs 14.00 crore."


def test_translation_with_no_digits_at_all_passes_trivially(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, "यह एक सरल वाक्य है।")
    result = run(translate_section_text("entry.a", "This is a simple sentence.", "hi"))
    assert result.translated is True
    assert result.text == "यह एक सरल वाक्य है।"


# --------------------------------------------------------------------------
# Provider selection: Sarvam preferred, general provider as backstop
# --------------------------------------------------------------------------


async def test_sarvam_is_used_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sarvam is Indic-first; the general provider translates Indian languages
    as a side capability, so it is the fallback, not the default."""
    monkeypatch.setattr("app.generate.translate.sarvam.is_available", lambda: True)

    async def _sarvam(text: str, lang: str) -> str:
        return "अंक 12.50 करोड़"

    monkeypatch.setattr("app.generate.translate.sarvam.translate", _sarvam)

    result = await translate_section_text("e1", "Issue size is Rs 12.50 crore.", "hi")
    assert result.translated is True
    assert result.text == "अंक 12.50 करोड़"


async def test_falls_through_to_the_general_provider_when_sarvam_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This feature has no deterministic fallback at all, so a second-choice
    translation beats none."""
    monkeypatch.setattr("app.generate.translate.sarvam.is_available", lambda: True)

    async def _boom(text: str, lang: str) -> str:
        raise LLMUnavailable("sarvam down")

    monkeypatch.setattr("app.generate.translate.sarvam.translate", _boom)

    async def _general(**_kwargs: object) -> LLMResponse:
        return LLMResponse(text="अंक 12.50 करोड़ (सामान्य)", provider="test", model="test")

    monkeypatch.setattr("app.generate.translate.grounded_complete", _general)

    result = await translate_section_text("e1", "Issue size is Rs 12.50 crore.", "hi")
    assert result.translated is True
    assert "सामान्य" in result.text


async def test_both_providers_down_shows_the_english_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.generate.translate.sarvam.is_available", lambda: True)

    async def _boom(*_a: object, **_k: object) -> object:
        raise LLMUnavailable("down")

    monkeypatch.setattr("app.generate.translate.sarvam.translate", _boom)
    monkeypatch.setattr("app.generate.translate.grounded_complete", _boom)

    english = "Issue size is Rs 12.50 crore."
    result = await translate_section_text("e1", english, "hi")
    assert result.translated is False
    assert result.text == english


async def test_a_sarvam_translation_inventing_a_number_is_still_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The number guard applies whichever provider produced the text."""
    monkeypatch.setattr("app.generate.translate.sarvam.is_available", lambda: True)

    async def _wrong_number(text: str, lang: str) -> str:
        return "अंक 99.99 करोड़"

    monkeypatch.setattr("app.generate.translate.sarvam.translate", _wrong_number)

    english = "Issue size is Rs 12.50 crore."
    result = await translate_section_text("e1", english, "hi")
    assert result.translated is False
    assert result.text == english
