"""Per-section grounded generation: deterministic path, citations, hallucination guard."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from app.facts import Fact, FactStore, Provenance, SourceKind
from app.generate import sections as sections_mod
from app.generate.sections import (
    format_inr_paise,
    generate_all,
    generate_section,
    requires_input_marker,
)
from app.llm.client import LLMResponse
from app.schema.loader import load_checklist
from app.schema.models import (
    Checklist,
    ChecklistEntry,
    ChecklistHeader,
    OutputTarget,
    Role,
    Severity,
)

CLAUSE = "ICDR Sch. VI Part A (as applied by Ch. IX), para 8"


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _entry(
    entry_id: str = "capital_structure.share_capital_history",
    required_facts: list[str] | None = None,
    role: Role = Role.PROMOTER,
    section: str = "Capital Structure",
) -> ChecklistEntry:
    return ChecklistEntry(
        id=entry_id,
        clause_ref=CLAUSE,
        section=section,
        title="History of equity share capital",
        description="Build-up of share capital since incorporation.",
        required_facts=required_facts or ["share_allotments[]"],
        responsible_role=role,
        severity=Severity.BLOCKER,
        output_targets=[OutputTarget.DRHP],
    )


def _confirmed_fact(store: FactStore, key: str, value: Any, detail: str) -> Fact:
    fact = store.add(
        Fact(
            key=key,
            value=value,
            provenance=Provenance(kind=SourceKind.WIZARD, detail=detail),
            supplied_by=Role.PROMOTER,
        )
    )
    return store.confirm(fact.fact_id)


def run(coro: Any) -> Any:  # noqa: ANN401 — asyncio bridge
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Deterministic path: no facts → missing_facts + [REQUIRES INPUT] marker
# --------------------------------------------------------------------------


def test_no_confirmed_facts_yields_requires_input_marker() -> None:
    entry = _entry(required_facts=["share_allotments[]"], role=Role.PROMOTER)
    store = FactStore()

    section = run(generate_section(entry, store))

    assert section.entry_id == entry.id
    assert section.missing_facts == ["share_allotments[]"]
    assert section.citations == []
    marker = requires_input_marker("share_allotments[]", "promoter")
    assert marker in section.text


# --------------------------------------------------------------------------
# Deterministic path: two facts under same key → two sentences,
# every citation span round-trips against section.text and contains value.
# --------------------------------------------------------------------------


def test_two_confirmed_facts_render_two_cited_sentences_and_spans_round_trip() -> None:
    entry = _entry(required_facts=["issue_size_paise"], role=Role.PROMOTER)
    store = FactStore()
    _confirmed_fact(store, "issue_size_paise", 14 * 10**9, "wizard:issue_size_v1")
    _confirmed_fact(store, "issue_size_paise", 15 * 10**9, "wizard:issue_size_v2")

    section = run(generate_section(entry, store))

    assert len(section.citations) == 2
    assert section.missing_facts == []

    # Every span must round-trip cleanly against section.text.
    for citation, expected_display in zip(
        section.citations,
        [format_inr_paise(14 * 10**9), format_inr_paise(15 * 10**9)],
        strict=True,
    ):
        start, end = citation.text_span
        assert 0 <= start < end <= len(section.text)
        span_text = section.text[start:end]
        assert expected_display in span_text, (
            f"expected display form {expected_display!r} in span {span_text!r}"
        )


# --------------------------------------------------------------------------
# Hallucination guard: monkeypatched grounded_complete returns a number that
# no provided fact accounts for → the deterministic fallback must win.
# --------------------------------------------------------------------------


def test_hallucination_guard_discards_llm_output_with_invented_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry(required_facts=["issue_size_paise"], role=Role.PROMOTER)
    store = FactStore()
    fact = _confirmed_fact(store, "issue_size_paise", 14 * 10**9, "wizard:issue_size")

    invented_number = "99999999999"
    # LLM tries to cite the real fact but slips in a bogus digit sequence.
    hallucinated_text = (
        f"The issue size is ₹{invented_number} paise [F:{fact.fact_id}]."
    )

    async def fake_grounded_complete(
        system: str,
        user: str,
        context_fact_ids: list[str],
        temperature: float = 0.0,
        **_kwargs: Any,
    ) -> LLMResponse:
        return LLMResponse(text=hallucinated_text, provider="fake", model="fake")

    monkeypatch.setattr(sections_mod, "grounded_complete", fake_grounded_complete)

    section = run(generate_section(entry, store))

    # Invented number is not in the shipped text.
    assert invented_number not in section.text
    # And the text is exactly what the deterministic renderer would produce.
    empty_store_repro = FactStore()
    reproduced = _confirmed_fact(
        empty_store_repro, "issue_size_paise", 14 * 10**9, "wizard:issue_size"
    )
    # We built a fresh confirmed fact — but its fact_id differs; instead
    # compare the *structure* of the deterministic output by regenerating
    # with the LLM patched to raise so the deterministic path is guaranteed.
    async def raising_grounded_complete(
        system: str,
        user: str,
        context_fact_ids: list[str],
        temperature: float = 0.0,
        **_kwargs: Any,
    ) -> LLMResponse:
        raise sections_mod.LLMUnavailable("forced offline for comparison")

    monkeypatch.setattr(sections_mod, "grounded_complete", raising_grounded_complete)
    deterministic = run(generate_section(entry, store))
    assert section.text == deterministic.text
    assert [c.fact_id for c in section.citations] == [
        c.fact_id for c in deterministic.citations
    ]
    # Silence "unused" — the repro block establishes we can rebuild the same
    # deterministic output from the same fact set.
    assert reproduced.value == fact.value


# --------------------------------------------------------------------------
# generate_all iterates non-stub always-applicable entries.
# --------------------------------------------------------------------------


def test_generate_all_returns_one_section_per_applicable_entry() -> None:
    checklist = Checklist(
        header=ChecklistHeader(
            regulation="test",
            amended_through="2026-03-21",
            schema_version="test",
            reviewed_by_human=True,
        ),
        entries=[
            _entry(entry_id="general.cover_pages", required_facts=["issue_size_paise"]),
            _entry(
                entry_id="capital_structure.share_capital_history",
                required_facts=["share_allotments[]"],
            ),
            # Stub is skipped:
            ChecklistEntry(
                id="stubbed.entry",
                clause_ref=CLAUSE,
                section="Stub",
                title="Stub",
                description="Stub",
                required_facts=[],
                responsible_role=Role.PROMOTER,
                severity=Severity.MINOR,
                output_targets=[OutputTarget.DRHP],
                stub=True,
            ),
            # Non-"always" applicability is skipped:
            ChecklistEntry(
                id="conditional.entry",
                clause_ref=CLAUSE,
                section="Conditional",
                title="Conditional",
                description="Conditional",
                applicability="has_convertibles",
                required_facts=["convertibles"],
                responsible_role=Role.PROMOTER,
                severity=Severity.MATERIAL,
                output_targets=[OutputTarget.DRHP],
            ),
        ],
    )
    store = FactStore()

    sections = run(generate_all(checklist, store))

    ids = [s.entry_id for s in sections]
    assert ids == ["general.cover_pages", "capital_structure.share_capital_history"]


def test_generate_all_smoke_over_real_checklist_produces_a_section_per_applicable_entry() -> (
    None
):
    """Belt-and-braces check against the real pinned schema — offline (no facts).

    Every non-stub always-applicable entry should surface exactly one
    GeneratedSection, all with empty citations and populated missing_facts.
    """
    checklist = load_checklist()
    store = FactStore()

    sections = run(generate_all(checklist, store))

    expected_ids = [
        e.id for e in checklist.entries if not e.stub and e.applicability == "always"
    ]
    assert [s.entry_id for s in sections] == expected_ids
    # Every section should have at least one [REQUIRES INPUT] marker because
    # no facts are confirmed.
    marker_re = re.compile(r"\[REQUIRES INPUT: .+ — .+ can provide this\]")
    for section in sections:
        assert marker_re.search(section.text), f"no marker in {section.entry_id}: {section.text!r}"
        assert section.citations == []
        assert section.missing_facts, (
            f"expected missing_facts on {section.entry_id} with an empty store"
        )


# --------------------------------------------------------------------------
# Definitions and abbreviations: system-authored glossary, never sent to LLM
# --------------------------------------------------------------------------


def test_definitions_section_renders_standard_glossary_without_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_called(**kwargs: Any) -> LLMResponse:
        raise AssertionError("definitions section must never call the LLM")

    monkeypatch.setattr(sections_mod, "grounded_complete", fail_if_called)

    entry = _entry(
        entry_id="general.definitions_abbreviations",
        required_facts=["issuer_identity"],
        role=Role.SYSTEM,
        section="General",
    )
    store = FactStore()
    section = run(generate_section(entry, store))

    assert "SEBI:" in section.text
    assert "ICDR Regulations:" in section.text
    assert "KMP:" in section.text
    assert section.missing_facts == ["issuer_identity"]
    assert "[REQUIRES INPUT: issuer_identity" in section.text


def test_definitions_section_includes_confirmed_issuer_identity_with_citation() -> None:
    entry = _entry(
        entry_id="general.definitions_abbreviations",
        required_facts=["issuer_identity"],
        role=Role.SYSTEM,
        section="General",
    )
    store = FactStore()
    fact = _confirmed_fact(
        store, "issuer_identity", {"name": "Sunrise Agrotech Ltd"}, "wizard:issuer_identity"
    )
    section = run(generate_section(entry, store))

    assert section.missing_facts == []
    assert any(c.fact_id == fact.fact_id for c in section.citations)
    span = section.citations[-1].text_span
    assert "Sunrise Agrotech Ltd" in section.text[span[0] : span[1]]
