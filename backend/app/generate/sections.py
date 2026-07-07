"""Per-section grounded generation.

Pipeline per schema entry: requirement → retrieve relevant confirmed facts →
grounded prompt → section text with citations and [REQUIRES INPUT] markers.

THE core rule: the LLM writes using only facts from the store. Anything
missing renders as ``[REQUIRES INPUT: <fact> — <who can provide it>]``.
Honest blanks beat confident hallucination — no exceptions.

Two rendering paths:

1. **LLM path** — a grounded prompt via ``app.llm.client.grounded_complete``
   (temperature 0). The model must append ``[F:<fact_id>]`` after every
   factual statement; markers are stripped in post-processing and turned into
   :class:`Citation` spans over the cleaned text.
2. **Deterministic renderer** — always available. Used when the LLM is
   unavailable (offline demo, no API key) *and* as the hallucination-guard
   fallback: one plain, fully cited sentence per confirmed fact. Every
   version returned by ``confirmed_by_key`` is rendered — duplicates are
   intentionally surfaced so the contradiction check can catch planted
   conflicts.

HALLUCINATION GUARD (non-negotiable — CLAUDE.md: one hallucinated number
destroys the trust pitch): after LLM generation, every digit sequence in the
cleaned text must be a substring / derivable display form of some provided
fact value, every citation must reference a provided fact, and text grounded
in facts must carry at least one citation. Any violation discards the LLM
text entirely in favour of the deterministic renderer.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from app.facts import Fact, FactStore
from app.llm.client import grounded_complete
from app.schema.applicability import entry_applies
from app.schema.models import Checklist, ChecklistEntry

try:  # pragma: no cover - exercised only once the client exports it
    from app.llm.client import LLMUnavailable  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - fallback until the client lands

    class LLMUnavailable(Exception):  # type: ignore[no-redef]
        """No LLM API key configured (mirrors ``app.llm.client.LLMUnavailable``)."""


class Citation(BaseModel):
    fact_id: str
    text_span: tuple[int, int]   # character offsets into the section text


class GeneratedSection(BaseModel):
    entry_id: str
    section: str
    text: str
    citations: list[Citation]
    missing_facts: list[str]     # fact keys rendered as [REQUIRES INPUT]


def requires_input_marker(fact_key: str, responsible_role: str) -> str:
    return f"[REQUIRES INPUT: {fact_key} — {responsible_role} can provide this]"


# --------------------------------------------------------------------------
# Display helpers (display layer ONLY — money stays integer paise everywhere)
# --------------------------------------------------------------------------

_PAISE_PER_CRORE = 10**9   # 1 crore rupees  = 10^7 rupees = 10^9 paise
_PAISE_PER_LAKH = 10**7    # 1 lakh rupees   = 10^5 rupees = 10^7 paise


def _two_decimals(magnitude: int, unit: int) -> str:
    """Integer-only half-up rounding of ``magnitude/unit`` to two decimals."""
    hundredths = (magnitude * 100 + unit // 2) // unit
    whole, frac = divmod(hundredths, 100)
    return f"{whole:,}.{frac:02d}"


def format_inr_paise(paise: int) -> str:
    """Format integer paise for display as ₹ lakh/crore. Never uses floats."""
    sign = "-" if paise < 0 else ""
    magnitude = abs(paise)
    if magnitude >= _PAISE_PER_CRORE:
        return f"{sign}₹{_two_decimals(magnitude, _PAISE_PER_CRORE)} crore"
    if magnitude >= _PAISE_PER_LAKH:
        return f"{sign}₹{_two_decimals(magnitude, _PAISE_PER_LAKH)} lakh"
    rupees, remainder = divmod(magnitude, 100)
    if remainder:
        return f"{sign}₹{rupees:,}.{remainder:02d}"
    return f"{sign}₹{rupees:,}"


def _humanize_key(key: str) -> str:
    """``share_allotments[]`` → ``Share allotments``; ``issue_size_paise`` → ``Issue size``."""
    base = key[:-2] if key.endswith("[]") else key
    if base.endswith("_paise"):
        base = base[: -len("_paise")]
    words = base.replace(".", " ").replace("_", " ").strip()
    if not words:
        return key
    return words[0].upper() + words[1:]


def _format_value(key: str, value: object) -> str:
    base = key[:-2] if key.endswith("[]") else key
    if base.endswith("_paise") and isinstance(value, int) and not isinstance(value, bool):
        return format_inr_paise(value)
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


# --------------------------------------------------------------------------
# Deterministic renderer (always available)
# --------------------------------------------------------------------------


def _fact_sentence(fact: Fact) -> str:
    return (
        f"{_humanize_key(fact.key)}: {_format_value(fact.key, fact.value)} "
        f"(source: {fact.provenance.detail})."
    )


def _render_deterministic(
    entry: ChecklistEntry,
    ordered_facts: list[Fact],
    missing: list[str],
) -> GeneratedSection:
    """One plain sentence per confirmed fact, each exactly covered by a citation span."""
    lines: list[str] = []
    citations: list[Citation] = []
    offset = 0
    for fact in ordered_facts:
        sentence = _fact_sentence(fact)
        citations.append(
            Citation(fact_id=fact.fact_id, text_span=(offset, offset + len(sentence)))
        )
        lines.append(sentence)
        offset += len(sentence) + 1  # +1 for the joining newline
    for key in missing:
        lines.append(requires_input_marker(key, entry.responsible_role.value))
    return GeneratedSection(
        entry_id=entry.id,
        section=entry.section,
        text="\n".join(lines),
        citations=citations,
        missing_facts=list(missing),
    )


# --------------------------------------------------------------------------
# Definitions and abbreviations: system-authored, never LLM-authored — a
# fixed regulatory glossary (per CLAUDE.md's Domain Glossary) plus
# issuer-specific terms pulled from confirmed facts. No hallucination risk
# because nothing here is generated text.
# --------------------------------------------------------------------------

_DEFINITIONS_ENTRY_ID = "general.definitions_abbreviations"

# Standard conventional/general and issue-related terms. Not derived from any
# fact — these are fixed statutory definitions, the same for every issuer.
_STANDARD_GLOSSARY: tuple[tuple[str, str], ...] = (
    ("SEBI", "Securities and Exchange Board of India, the capital markets regulator."),
    (
        "ICDR Regulations",
        "SEBI (Issue of Capital and Disclosure Requirements) Regulations, 2018, as amended.",
    ),
    ("Chapter IX", "The ICDR chapter governing SME initial public offers."),
    ("DRHP", "Draft Red Herring Prospectus, filed for regulatory and public review."),
    ("RHP", "Red Herring Prospectus, the updated offer document filed before the issue opens."),
    (
        "SME Exchange",
        "The dedicated SME listing platform of a recognised stock exchange "
        "(e.g. BSE SME, NSE Emerge).",
    ),
    (
        "Lead Manager / Merchant Banker",
        "The SEBI-registered intermediary responsible for due diligence and "
        "certification of the issue.",
    ),
    ("KMP", "Key Managerial Personnel."),
    ("RPT", "Related Party Transaction."),
    ("OFS", "Offer for Sale — existing shareholders selling shares as part of the issue."),
    (
        "GCP",
        "General Corporate Purposes — a use-of-proceeds category subject to a regulatory cap.",
    ),
    ("Promoter", "A person or entity in control of the issuer, per ICDR Regulation 2(1)(oo)."),
    (
        "Promoter Group",
        "Persons and entities constituting the promoter group under ICDR Regulation 2(1)(pp).",
    ),
)


def _render_definitions_abbreviations(
    entry: ChecklistEntry, issuer_facts: list[Fact], missing: list[str]
) -> GeneratedSection:
    lines: list[str] = [f"{term}: {meaning}" for term, meaning in _STANDARD_GLOSSARY]
    citations: list[Citation] = []
    offset = sum(len(line) + 1 for line in lines)  # glossary lines carry no citation
    for fact in issuer_facts:
        sentence = _fact_sentence(fact)
        citations.append(
            Citation(fact_id=fact.fact_id, text_span=(offset, offset + len(sentence)))
        )
        lines.append(sentence)
        offset += len(sentence) + 1
    for key in missing:
        lines.append(requires_input_marker(key, entry.responsible_role.value))
    return GeneratedSection(
        entry_id=entry.id,
        section=entry.section,
        text="\n".join(lines),
        citations=citations,
        missing_facts=list(missing),
    )


# --------------------------------------------------------------------------
# LLM path: grounded prompt → citation extraction → hallucination guard
# --------------------------------------------------------------------------

_MARKER_RE = re.compile(r"[ \t]*\[F:([^\]\s]+)\]")
_DIGITS_RE = re.compile(r"\d+")

_SYSTEM_PROMPT = (
    "You are drafting one disclosure section of an SME IPO draft offer document (DRHP) "
    "under SEBI ICDR Chapter IX. Write the disclosure text for the given requirement "
    "using ONLY the facts provided. After every factual statement, append a citation "
    "marker of the exact form [F:<fact_id>] using the fact_id of the supporting fact. "
    "Never introduce a number, name, date, amount, or clause reference that is not "
    "present in the provided facts. If a detail is not in the facts, omit it entirely."
)


def _build_user_prompt(entry: ChecklistEntry, facts: list[Fact]) -> str:
    facts_payload = [
        {
            "fact_id": fact.fact_id,
            "key": fact.key,
            "value": fact.value,
            "provenance": fact.provenance.detail,
        }
        for fact in facts
    ]
    return (
        f"Requirement: {entry.title}\n"
        f"Clause: {entry.clause_ref}\n"
        f"Section: {entry.section}\n"
        f"Description: {entry.description}\n\n"
        "Facts (JSON):\n"
        f"{json.dumps(facts_payload, ensure_ascii=False, default=str)}"
    )


def _extract_citations(raw: str) -> tuple[str, list[Citation]]:
    """Strip ``[F:<fact_id>]`` markers; return cleaned text + spans over it.

    Each citation span covers the cleaned-text region between the previous
    marker (or the start of the text) and this marker, trimmed of surrounding
    whitespace — i.e. the statement the marker vouches for.
    """
    parts: list[str] = []
    provisional: list[tuple[str, int, int]] = []
    cleaned_len = 0
    span_start = 0
    last_end = 0
    for match in _MARKER_RE.finditer(raw):
        segment = raw[last_end : match.start()]
        parts.append(segment)
        cleaned_len += len(segment)
        provisional.append((match.group(1), span_start, cleaned_len))
        span_start = cleaned_len
        last_end = match.end()
    parts.append(raw[last_end:])
    cleaned = "".join(parts)

    citations: list[Citation] = []
    for fact_id, start, end in provisional:
        while start < end and cleaned[start].isspace():
            start += 1
        while end > start and cleaned[end - 1].isspace():
            end -= 1
        if end > start:
            citations.append(Citation(fact_id=fact_id, text_span=(start, end)))
    return cleaned, citations


def _fact_value_representations(facts: list[Fact]) -> list[str]:
    """Every string form a fact value may legitimately take in generated text."""
    reps: list[str] = []
    for fact in facts:
        if isinstance(fact.value, dict | list):
            raw = json.dumps(fact.value, ensure_ascii=False, sort_keys=True, default=str)
        else:
            raw = str(fact.value)
        display = _format_value(fact.key, fact.value)
        reps.extend((raw, raw.replace(",", ""), display, display.replace(",", "")))
    return reps


def _passes_hallucination_guard(
    cleaned: str, citations: list[Citation], facts: list[Fact]
) -> bool:
    """False → the LLM text must be discarded in favour of the deterministic renderer."""
    if not cleaned.strip():
        return False
    if facts and not citations:
        return False  # grounded facts exist but nothing is cited
    known_ids = {fact.fact_id for fact in facts}
    if any(citation.fact_id not in known_ids for citation in citations):
        return False  # citation to a fact that was never provided
    reps = _fact_value_representations(facts)
    for digits in _DIGITS_RE.findall(cleaned):
        if not any(digits in rep for rep in reps):
            return False  # a number that no provided fact can account for
    return True


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------


async def generate_section(entry: ChecklistEntry, store: FactStore) -> GeneratedSection:
    """Generate one section grounded in confirmed facts only.

    Retrieves every confirmed fact for ``entry.required_facts`` (all versions
    — duplicates feed the contradiction check), attempts grounded LLM
    generation, and falls back to the deterministic renderer when the LLM is
    unavailable or its output fails the hallucination guard. Keys with no
    confirmed fact are listed in ``missing_facts`` and rendered as
    ``[REQUIRES INPUT]`` marker lines.
    """
    facts_by_key = {key: store.confirmed_by_key(key) for key in entry.required_facts}
    missing = [key for key, found in facts_by_key.items() if not found]
    ordered_facts = [fact for found in facts_by_key.values() for fact in found]

    if entry.id == _DEFINITIONS_ENTRY_ID:
        # System-authored glossary, never sent to the LLM.
        return _render_definitions_abbreviations(entry, ordered_facts, missing)

    if not ordered_facts:
        return _render_deterministic(entry, [], missing)

    try:
        response = await grounded_complete(
            system=_SYSTEM_PROMPT,
            user=_build_user_prompt(entry, ordered_facts),
            context_fact_ids=[fact.fact_id for fact in ordered_facts],
        )
    except (LLMUnavailable, NotImplementedError):
        # Offline-first: no API key (or provider not yet wired) → deterministic path.
        return _render_deterministic(entry, ordered_facts, missing)

    cleaned, citations = _extract_citations(response.text)
    if not _passes_hallucination_guard(cleaned, citations, ordered_facts):
        # Non-negotiable: discard the whole LLM text rather than ship one bad number.
        return _render_deterministic(entry, ordered_facts, missing)

    text = cleaned.rstrip()  # rstrip only — leading offsets must stay valid
    if missing:
        markers = "\n".join(
            requires_input_marker(key, entry.responsible_role.value) for key in missing
        )
        text = f"{text}\n{markers}" if text else markers
    return GeneratedSection(
        entry_id=entry.id,
        section=entry.section,
        text=text,
        citations=citations,
        missing_facts=missing,
    )


async def generate_all(checklist: Checklist, store: FactStore) -> list[GeneratedSection]:
    """Generate every currently applicable section of the checklist.

    Covers all non-stub entries whose ``applicability`` holds for this issuer:
    ``"always"``, or a named ``has_<fact_key>`` condition evaluated against
    the confirmed facts (see :mod:`app.schema.applicability`).
    """
    sections: list[GeneratedSection] = []
    for entry in checklist.entries:
        if entry.stub or not entry_applies(entry, store):
            continue
        sections.append(await generate_section(entry, store))
    return sections
