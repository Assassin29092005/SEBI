"""Vernacular draft translation — extends the wizard's existing EN/HI
question-copy convention (see ``app.intake.wizard.SUPPORTED_LANGUAGES``) to
the generated draft itself, for promoter *review* only.

The filed DRHP stays English — that is a regulatory reality, not a UX
choice, so this never touches assembly (``app.assemble``) or what gets
exported. The gap this closes: a promoter who can answer every wizard
question in Hindi previously had no way to read back what the tool actually
wrote in review — they could confirm facts in their own language but had to
verify the generated prose in English regardless. This is a demo-toggle
made real for daily use: the same language a promoter already works in
end-to-end, not just at intake.

Unlike every other LLM-touching feature in this app, there is no
deterministic fallback — translation has no non-LLM implementation. When no
provider is configured (or the call fails), ``translated=False`` and
``text`` degrades to the original English exactly as generated: never a
blank page, never an error the promoter has to parse, just an honest signal
that this particular request didn't get translated.

Guard: a translation must never introduce a number that was not already in
the approved English text. That text was already ground-guarded once, by
``app.generate.sections``'s own hallucination guard, when it was first
generated — this only has to prove translation didn't ADD to what was
already approved, not re-derive fact provenance from scratch. Every digit
sequence in the translated text must appear verbatim somewhere in the
original; if even one doesn't, the translation is discarded in favour of
the English original, exactly like ``generate_section`` discards LLM output
that fails its own guard.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from app.intake.wizard import SUPPORTED_LANGUAGES
from app.llm.client import grounded_complete

try:  # pragma: no cover - exercised only once the client exports it
    from app.llm.client import LLMUnavailable  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - fallback until the client lands

    class LLMUnavailable(Exception):
        """No LLM API key configured (mirrors ``app.llm.client.LLMUnavailable``)."""


_LLM_SKIP_ERRORS: tuple[type[Exception], ...] = (LLMUnavailable, NotImplementedError)

_DIGITS_RE = re.compile(r"\d+")

# Display names for the translation prompt — SUPPORTED_LANGUAGES is the
# authoritative code list (shared with the wizard); this is just prose for
# the system prompt, not a second source of truth on what's supported.
_LANGUAGE_NAMES: dict[str, str] = {"hi": "Hindi"}

_SYSTEM_PROMPT_TEMPLATE = (
    "Translate the following section of an SME IPO draft offer document "
    "(DRHP) into {language}, for a promoter reviewing their own draft. "
    "Translate the ENTIRE text literally and completely — never summarise, "
    "shorten, or omit any sentence. Never add, remove, or alter any number, "
    "date, amount, percentage, or proper name. Preserve numerals and clause/"
    "regulation references exactly as they appear in the source text. "
    "Output ONLY the translated text — no preamble, no commentary, no "
    "markdown formatting."
)


class TranslatedSection(BaseModel):
    entry_id: str
    lang: str
    text: str
    # False means `text` is the untranslated English original, shown as an
    # honest fallback (no LLM configured, the call failed, or the result
    # failed the number-preservation guard below) — never a blank/error.
    translated: bool


def _preserves_numbers(original: str, translated: str) -> bool:
    """Every digit sequence in ``translated`` must appear verbatim in ``original``.

    Deliberately simpler than app.generate.sections's fact-provenance guard:
    the source text was already fact-grounded once, so this only has to
    prove the translation didn't introduce a NEW number relative to that
    already-approved text — a plain substring check over both texts, no
    fact store or checklist required.
    """
    return all(digits in original for digits in _DIGITS_RE.findall(translated))


async def translate_section_text(
    entry_id: str, text: str, lang: str
) -> TranslatedSection:
    """Translate one already-generated section's text into ``lang``.

    ``lang == "en"`` (or any language outside SUPPORTED_LANGUAGES) is a
    trivial no-op — the text is already English, translating it to itself
    would just be a wasted LLM call. An empty/whitespace-only section has
    nothing to translate either.
    """
    if lang == "en" or lang not in SUPPORTED_LANGUAGES:
        return TranslatedSection(entry_id=entry_id, lang="en", text=text, translated=True)
    if not text.strip():
        return TranslatedSection(entry_id=entry_id, lang=lang, text=text, translated=True)

    language_name = _LANGUAGE_NAMES.get(lang, lang)
    system = _SYSTEM_PROMPT_TEMPLATE.format(language=language_name)
    try:
        response = await grounded_complete(
            system=system,
            user=text,
            context_fact_ids=[],  # translation, not fact-grounded generation
            temperature=0.0,
        )
    except _LLM_SKIP_ERRORS:
        return TranslatedSection(entry_id=entry_id, lang=lang, text=text, translated=False)

    translated_text = response.text.strip()
    if not translated_text or not _preserves_numbers(text, translated_text):
        return TranslatedSection(entry_id=entry_id, lang=lang, text=text, translated=False)

    return TranslatedSection(entry_id=entry_id, lang=lang, text=translated_text, translated=True)
