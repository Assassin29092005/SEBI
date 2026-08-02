"""Iterative adversarial examiner (app.validate.iterative_examiner): the
revise-and-re-examine loop, verified offline and with a patched LLM.

Every scenario here was hand-verified against a standalone script before
being written up as a formal test.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from app.facts import Fact, FactStore, Provenance, SourceKind
from app.generate import sections as sections_mod
from app.generate.sections import Citation, GeneratedSection
from app.llm.client import LLMResponse
from app.schema.models import (
    Checklist,
    ChecklistEntry,
    ChecklistHeader,
    OutputTarget,
    Role,
    Severity,
)
from app.validate.iterative_examiner import examine_iteratively

CLAUSE = "ICDR Sch. VI Part A (as applied by Ch. IX), para 9(A)"
ENTRY_ID = "capital_structure.share_capital_history"


def make_checklist() -> Checklist:
    return Checklist(
        header=ChecklistHeader(
            regulation="SEBI ICDR Regulations, 2018 — Chapter IX",
            amended_through="2026-03-21",
            schema_version="test",
            reviewed_by_human=True,
        ),
        entries=[
            ChecklistEntry(
                id=ENTRY_ID,
                clause_ref=CLAUSE,
                section="Capital Structure",
                title="History of equity share capital",
                description="Build-up of share capital since incorporation.",
                required_facts=["share_allotments"],
                responsible_role=Role.PROMOTER,
                severity=Severity.BLOCKER,
                output_targets=[OutputTarget.DRHP],
            )
        ],
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _confirmed_fact(store: FactStore, *, value: object) -> Fact:
    fact = store.add(
        Fact(
            key="share_allotments",
            value=value,
            provenance=Provenance(kind=SourceKind.WIZARD, detail="q:share_allotments"),
            confidence=1.0,
            supplied_by=Role.PROMOTER,
        )
    )
    store.confirm(fact.fact_id)
    return fact


# --------------------------------------------------------------------------
# Convergence without any objections
# --------------------------------------------------------------------------


def test_already_clean_section_survives_round_1() -> None:
    checklist = make_checklist()
    store = FactStore()
    text = "The company allotted 1,00,000 equity shares of face value Rs 10 each."
    section = GeneratedSection(
        entry_id=ENTRY_ID,
        section="Capital Structure",
        text=text,
        citations=[Citation(fact_id="fact-1", text_span=(0, len(text)))],
        missing_facts=[],
    )
    report = run(examine_iteratively([section], checklist, store))

    assert len(report.rounds) == 1
    assert report.rounds[0].objections == []
    assert report.stop_reason == "survived"
    assert report.survived is True
    assert report.final_objections == []
    assert report.final_sections == [section]


# --------------------------------------------------------------------------
# Structural objections (data problems) never trigger a wasted revision round
# --------------------------------------------------------------------------


def test_structural_only_objection_stops_without_revision_round() -> None:
    checklist = make_checklist()
    store = FactStore()
    section = GeneratedSection(
        entry_id=ENTRY_ID,
        section="Capital Structure",
        text="Pending.",
        citations=[],
        missing_facts=["share_allotments"],
    )
    report = run(examine_iteratively([section], checklist, store))

    # No revision attempted — a missing fact can't be fixed by a rewrite —
    # so the loop stops after exactly the one examining round.
    assert len(report.rounds) == 1
    assert report.stop_reason == "no_revisable_objections"
    assert report.survived is False
    assert len(report.final_objections) == 1
    assert "Missing required fact" in report.final_objections[0].objection
    assert report.final_sections == [section]  # untouched


# --------------------------------------------------------------------------
# Non-structural (boilerplate) objection triggers a real revision round
# --------------------------------------------------------------------------


def test_boilerplate_objection_triggers_revision_round() -> None:
    checklist = make_checklist()
    store = FactStore()
    fact = _confirmed_fact(store, value="1,00,000 equity shares allotted on 2020-04-01")

    text = "We are a leading player poised for growth in the agrotech sector."
    section = GeneratedSection(
        entry_id=ENTRY_ID,
        section="Capital Structure",
        text=text,
        citations=[Citation(fact_id=fact.fact_id, text_span=(0, len(text)))],
        missing_facts=[],
    )
    report = run(examine_iteratively([section], checklist, store, max_rounds=3))

    # Round 1 raised the boilerplate objections (two filler phrases).
    assert len(report.rounds[0].objections) == 2
    assert all("boilerplate" in o.objection for o in report.rounds[0].objections)
    assert report.rounds[0].revised_entry_ids == []  # nothing revised BEFORE round 1

    # Offline: generate_section's LLM call has no key configured and raises,
    # so revision falls back to the deterministic renderer — a fresh,
    # non-boilerplate rendering. Round 2 comes back clean.
    assert len(report.rounds) == 2
    assert report.rounds[1].revised_entry_ids == [ENTRY_ID]
    assert report.stop_reason == "survived"
    assert report.survived is True
    assert report.final_sections[0].text != text
    assert "leading player" not in report.final_sections[0].text


# --------------------------------------------------------------------------
# max_rounds is an honest budget cap, not a silent success
# --------------------------------------------------------------------------


def test_max_rounds_cap_stops_with_objections_still_open() -> None:
    checklist = make_checklist()
    store = FactStore()
    text = "We are a leading player poised for growth in the agrotech sector."
    section = GeneratedSection(
        entry_id=ENTRY_ID,
        section="Capital Structure",
        text=text,
        citations=[Citation(fact_id="fact-1", text_span=(0, len(text)))],
        missing_facts=[],
    )
    report = run(examine_iteratively([section], checklist, store, max_rounds=1))

    assert len(report.rounds) == 1
    assert report.stop_reason == "max_rounds_reached"
    assert report.survived is False


def test_max_rounds_below_one_is_rejected() -> None:
    checklist = make_checklist()
    store = FactStore()
    with pytest.raises(ValueError):
        run(examine_iteratively([], checklist, store, max_rounds=0))


# --------------------------------------------------------------------------
# With a real (patched) LLM available: revision genuinely rewrites the text,
# folding the objection feedback into the prompt — not just falling back.
# --------------------------------------------------------------------------


def test_llm_revision_resolves_the_objection_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checklist = make_checklist()
    store = FactStore()
    fact = _confirmed_fact(store, value="1,00,000 equity shares allotted on 2020-04-01")

    text = "We are a leading player poised for growth in the agrotech sector."
    section = GeneratedSection(
        entry_id=ENTRY_ID,
        section="Capital Structure",
        text=text,
        citations=[Citation(fact_id=fact.fact_id, text_span=(0, len(text)))],
        missing_facts=[],
    )

    async def fake_examiner_llm(
        system: str, user: str, context_fact_ids: list[str], temperature: float = 0.0
    ) -> LLMResponse:
        return LLMResponse(text="[]", provider="fake", model="fake")

    seen_prompts: list[str] = []

    async def fake_generation_llm(
        system: str, user: str, context_fact_ids: list[str], temperature: float = 0.0
    ) -> LLMResponse:
        seen_prompts.append(user)
        rewritten = (
            f"The company allotted 1,00,000 equity shares on 2020-04-01. "
            f"[F:{context_fact_ids[0]}]"
        )
        return LLMResponse(text=rewritten, provider="fake", model="fake")

    monkeypatch.setattr("app.llm.client.grounded_complete", fake_examiner_llm)
    monkeypatch.setattr(sections_mod, "grounded_complete", fake_generation_llm)

    report = run(examine_iteratively([section], checklist, store, max_rounds=3))

    assert len(report.rounds) == 2
    assert report.stop_reason == "survived"
    assert report.survived is True
    # The revision prompt actually carried the objection feedback.
    assert seen_prompts, "generation LLM was never called for revision"
    assert "leading player" in seen_prompts[0] or "poised for growth" in seen_prompts[0]
    assert "reviewer raised the following objections" in seen_prompts[0]
    # And the shipped text is the LLM's rewrite, not a deterministic fallback.
    assert "leading player" not in report.final_sections[0].text
    assert "1,00,000 equity shares" in report.final_sections[0].text
