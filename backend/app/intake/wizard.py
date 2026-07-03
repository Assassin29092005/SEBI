"""Wizard question flow — DERIVED from the checklist schema, never hand-maintained.

Every question shows *why* it is asked, with the regulation clause it maps to.
Prompt copy is stored in ``question_copy.yaml`` (English + demo-grade Hindi);
the schema still owns the ``clause_ref`` and ``description`` for every entry.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from app.schema.models import Checklist, Role

_COPY_PATH = Path(__file__).parent / "question_copy.yaml"

# Languages the wizard advertises. English is the guaranteed fallback.
SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "hi")
_FALLBACK_LANGUAGE = "en"


class WizardQuestion(BaseModel):
    fact_key: str
    section: str
    prompt: str            # plain-language question text (lang-specific)
    why_we_ask: str        # promoter-facing explanation (from schema)
    clause_ref: str        # the regulation clause this maps to (from schema)
    checklist_entry_id: str
    input_hint: str = "text"  # UI hint: "money" | "list" | "date" | "text"
    help_text: str | None = None  # optional longer plain-language help (lang-specific)


@cache
def _load_copy() -> dict[str, dict[str, dict[str, str]]]:
    """Read the multilingual question-copy YAML once and cache it.

    Shape: ``{fact_key: {lang: {"prompt": str, "help": str}}}``.
    Missing file collapses to an empty dict — the wizard still works using
    humanised-key fallback prompts (offline-first).
    """
    if not _COPY_PATH.exists():
        return {}
    raw: Any = yaml.safe_load(_COPY_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    normalised: dict[str, dict[str, dict[str, str]]] = {}
    for key, langs in raw.items():
        if not isinstance(langs, dict):
            continue
        bucket: dict[str, dict[str, str]] = {}
        for lang, fields in langs.items():
            if not isinstance(fields, dict):
                continue
            prompt = str(fields.get("prompt", "")).strip()
            help_text = str(fields.get("help", "")).strip()
            if prompt:
                bucket[str(lang)] = {"prompt": prompt, "help": help_text}
        if bucket:
            normalised[str(key)] = bucket
    return normalised


def derive_questions(checklist: Checklist, lang: str = "en") -> list[WizardQuestion]:
    """Build the promoter question flow from schema entries.

    Only promoter-role, non-stub entries yield wizard questions; auditor- and
    banker-role requirements route to uploads in their respective workflows.

    ``lang`` selects UX copy (``"en"`` or ``"hi"``). Unknown languages, or
    keys with no copy in the requested language, fall back to English, and
    finally to a humanised version of the fact key. Fallback never raises —
    an unknown fact key still yields a usable question.
    """
    copy = _load_copy()
    questions: list[WizardQuestion] = []
    for entry in checklist.entries:
        if entry.stub or entry.responsible_role != Role.PROMOTER:
            continue
        for fact_key in entry.required_facts:
            prompt, help_text = _resolve_copy(copy, fact_key, lang)
            questions.append(
                WizardQuestion(
                    fact_key=fact_key,
                    section=entry.section,
                    prompt=prompt,
                    why_we_ask=entry.description.strip(),
                    clause_ref=entry.clause_ref,
                    checklist_entry_id=entry.id,
                    input_hint=_input_hint_for(fact_key),
                    help_text=help_text or None,
                )
            )
    return questions


def _resolve_copy(
    copy: dict[str, dict[str, dict[str, str]]],
    fact_key: str,
    lang: str,
) -> tuple[str, str]:
    """Return ``(prompt, help_text)`` for ``fact_key`` in ``lang``.

    Fallback order: requested lang → English → humanised key. Never raises.
    """
    bucket = copy.get(fact_key, {})
    for candidate in (lang, _FALLBACK_LANGUAGE):
        entry = bucket.get(candidate)
        if entry and entry.get("prompt"):
            return entry["prompt"], entry.get("help", "")
    return _humanise_fact_key(fact_key), ""


def _humanise_fact_key(fact_key: str) -> str:
    """Fallback prompt when no copy exists — e.g. ``kmp[]`` → 'Please provide: kmp'."""
    cleaned = fact_key.removesuffix("[]").replace("_", " ").strip()
    if not cleaned:
        cleaned = fact_key
    return f"Please provide: {cleaned}"


def _input_hint_for(fact_key: str) -> str:
    """Heuristic UI hint from the fact-key naming convention.

    Rules (checked in order):
      * ends with ``_paise``  → ``"money"`` (INR integer, formatted at display)
      * ends with ``[]``      → ``"list"`` (repeatable structured rows)
      * contains ``date`` or ends with ``_date`` → ``"date"``
      * otherwise             → ``"text"``
    """
    if fact_key.endswith("_paise"):
        return "money"
    if fact_key.endswith("[]"):
        return "list"
    if fact_key.endswith("_date") or "date" in fact_key:
        return "date"
    return "text"
