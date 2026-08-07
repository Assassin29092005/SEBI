"""Sarvam translation client: chunking, language mapping, offline behaviour.

The chunker is the part that fails silently. Sarvam caps a request at 1000
characters (mayura:v1) or 2000 (sarvam-translate:v1), and real generated
sections in this app run to ~2066 — so every section over the cap either gets
split correctly or gets rejected, and a rejected chunk means the promoter
silently sees English with no explanation. Splitting mid-number is worse
still: the caller's guard requires every digit sequence in the translation to
appear verbatim in the English original, so a number cut across two requests
discards an otherwise good translation.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.llm.client import LLMUnavailable
from app.llm.sarvam import _LANGUAGE_CODES, is_available, split_for_translation, translate


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def test_short_text_is_one_chunk() -> None:
    assert split_for_translation("Issue size is Rs 12.50 crore.", 1000) == [
        "Issue size is Rs 12.50 crore."
    ]


def test_empty_text_yields_no_chunks() -> None:
    assert split_for_translation("", 1000) == []
    assert split_for_translation("   \n\n  ", 1000) == []


def test_paragraphs_split_before_sentences() -> None:
    text = "First paragraph here.\n\nSecond paragraph here."
    assert split_for_translation(text, 1000) == [
        "First paragraph here.",
        "Second paragraph here.",
    ]


def test_a_long_paragraph_is_packed_into_whole_sentences() -> None:
    sentence = "The issuer proposes to raise capital for working capital needs. "
    chunks = split_for_translation(sentence * 30, 300)

    assert len(chunks) > 1
    assert all(len(c) <= 300 for c in chunks)
    # Nothing is dropped: every sentence survives somewhere.
    assert sum(c.count("The issuer proposes") for c in chunks) == 30


def test_a_decimal_amount_is_never_split_across_chunks() -> None:
    """The whole point of the sentence-boundary rule.

    "Rs 12.50 crore" contains a period. Splitting naively on "." would cut it
    into "Rs 12." and "50 crore", and "50" would then not appear verbatim in
    the English original — the caller would discard the translation and show
    English, with nothing explaining why.
    """
    text = "Issue size is Rs 12.50 crore. " * 20
    chunks = split_for_translation(text, 200)

    assert all("12.50" in c or "12.50" not in text[: len(c)] for c in chunks)
    for chunk in chunks:
        # No chunk ends mid-number or begins with an orphaned fragment.
        assert not chunk.rstrip().endswith("12.")
        assert not chunk.lstrip().startswith("50 crore")


def test_a_single_oversized_sentence_is_passed_through_whole() -> None:
    """Better a rejected request than half a sentence presented as complete."""
    giant = "word " * 500  # one 'sentence', no terminator
    chunks = split_for_translation(giant, 100)
    assert len(chunks) == 1
    assert chunks[0] == giant.strip() or chunks[0] == giant


def test_every_real_section_length_chunks_within_the_limit() -> None:
    """Sized against what this app actually generates: median ~865 chars,
    longest ~2066."""
    for length in (78, 865, 2066, 5000):
        text = ("The issuer confirms the following disclosure. " * (length // 45 + 1))[:length]
        for limit in (950, 1950):
            assert all(len(c) <= limit for c in split_for_translation(text, limit))


# --------------------------------------------------------------------------
# Availability + language mapping
# --------------------------------------------------------------------------


def test_unavailable_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sarvam_api_key", "")
    assert is_available() is False


def test_available_with_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sarvam_api_key", "sk-test")
    assert is_available() is True


async def test_translate_without_a_key_raises_rather_than_calling_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "sarvam_api_key", "")
    with pytest.raises(LLMUnavailable):
        await translate("Issue size is Rs 12.50 crore.", "hi")


async def test_an_unmapped_language_raises_instead_of_guessing_a_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong target_language_code would be a paid request returning the
    wrong language, which the number guard cannot catch."""
    monkeypatch.setattr(settings, "sarvam_api_key", "sk-test")
    with pytest.raises(LLMUnavailable, match="no language code"):
        await translate("Issue size is Rs 12.50 crore.", "fr")


def test_hindi_maps_to_sarvams_own_code() -> None:
    # Sarvam wants "hi-IN"; the wizard and this app use "hi".
    assert _LANGUAGE_CODES["hi"] == "hi-IN"
