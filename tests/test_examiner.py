"""Adversarial examiner: deterministic objections offline, sanitised LLM objections online."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.generate.sections import Citation, GeneratedSection, requires_input_marker
from app.llm.client import LLMResponse
from app.schema.models import (
    Checklist,
    ChecklistEntry,
    ChecklistHeader,
    OutputTarget,
    Role,
    Severity,
)
from app.validate import examiner
from app.validate.examiner import Objection, examine

CLAUSE = "ICDR Sch. VI Part A (as applied by Ch. IX), para 9(A)"


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
                id="capital_structure.share_capital_history",
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


def make_section(
    *,
    text: str,
    citations: list[Citation] | None = None,
    missing_facts: list[str] | None = None,
) -> GeneratedSection:
    return GeneratedSection(
        entry_id="capital_structure.share_capital_history",
        section="Capital Structure",
        text=text,
        citations=citations or [],
        missing_facts=missing_facts or [],
    )


def run(coro: Any) -> list[Objection]:  # noqa: ANN401 — thin asyncio bridge
    return asyncio.run(coro)


# --- deterministic pass (offline: the stub LLM providers raise, examine must not) ---


def test_missing_fact_raises_objection_naming_key_and_supplier() -> None:
    marker = requires_input_marker("share_allotments", "promoter")
    section = make_section(
        text=f"The build-up of capital is as follows. {marker}",
        missing_facts=["share_allotments"],
    )
    objections = run(examine([section], checklist=make_checklist()))

    assert len(objections) == 1
    obj = objections[0]
    assert obj.entry_id == "capital_structure.share_capital_history"
    assert "share_allotments" in obj.objection
    assert "promoter" in obj.objection
    assert obj.clause_ref == CLAUSE
    assert obj.resolved is False


def test_missing_fact_objection_without_checklist_has_no_clause_ref() -> None:
    section = make_section(text="Pending.", missing_facts=["share_allotments"])
    objections = run(examine([section]))

    assert len(objections) == 1
    assert "share_allotments" in objections[0].objection
    assert objections[0].clause_ref is None


def test_fully_cited_clean_section_yields_no_deterministic_objections() -> None:
    text = "The company allotted 1,00,000 equity shares of face value Rs 10 each."
    section = make_section(
        text=text,
        citations=[Citation(fact_id="fact-1", text_span=(0, len(text)))],
    )
    objections = run(examine([section], checklist=make_checklist()))
    assert objections == []


def test_digits_without_citations_raise_uncited_quantitative_claim() -> None:
    section = make_section(text="The company allotted 100000 equity shares in 2019.")
    objections = run(examine([section], checklist=make_checklist()))

    assert len(objections) == 1
    assert "ncited quantitative claim" in objections[0].objection
    assert objections[0].clause_ref == CLAUSE


def test_digits_only_inside_requires_input_marker_are_not_quantitative_claims() -> None:
    marker = requires_input_marker("fy2024_revenue", "auditor")
    section = make_section(
        text=f"Revenue for the period: {marker}",
        missing_facts=["fy2024_revenue"],
    )
    objections = run(examine([section]))

    # Only the missing-fact objection; the digits live inside the marker.
    assert len(objections) == 1
    assert "fy2024_revenue" in objections[0].objection
    assert "auditor" in objections[0].objection


# --- LLM pass: clause_ref sanitisation and offline skip ---


def _patch_llm(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    async def fake_grounded_complete(
        system: str,
        user: str,
        context_fact_ids: list[str],
        temperature: float = 0.0,
    ) -> LLMResponse:
        assert temperature == 0.0
        return LLMResponse(text=text, provider="fake", model="fake")

    monkeypatch.setattr("app.llm.client.grounded_complete", fake_grounded_complete)


def _clean_cited_section() -> GeneratedSection:
    text = "The issue comprises 20,00,000 shares."
    return GeneratedSection(
        entry_id="capital_structure.share_capital_history",
        section="Capital Structure",
        text=text,
        citations=[Citation(fact_id="fact-1", text_span=(0, len(text)))],
        missing_facts=[],
    )


def test_llm_foreign_clause_ref_is_sanitised_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(
        monkeypatch,
        '[{"entry_id": "capital_structure.share_capital_history", '
        '"objection": "Allotment dates are not disclosed.", '
        '"clause_ref": "ICDR Reg. 999(9) (invented)"}]',
    )
    objections = run(examine([_clean_cited_section()], checklist=make_checklist()))

    assert len(objections) == 1
    assert objections[0].objection == "Allotment dates are not disclosed."
    assert objections[0].clause_ref is None  # never invent clause citations
    assert objections[0].resolved is False


def test_llm_matching_clause_ref_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(
        monkeypatch,
        f'[{{"entry_id": "capital_structure.share_capital_history", '
        f'"objection": "Nature of consideration is not stated.", '
        f'"clause_ref": "{CLAUSE}"}}]',
    )
    objections = run(examine([_clean_cited_section()], checklist=make_checklist()))

    assert len(objections) == 1
    assert objections[0].clause_ref == CLAUSE


def test_llm_clause_ref_without_checklist_is_sanitised_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm(
        monkeypatch,
        f'[{{"entry_id": "capital_structure.share_capital_history", '
        f'"objection": "Lock-in details are missing.", "clause_ref": "{CLAUSE}"}}]',
    )
    objections = run(examine([_clean_cited_section()]))  # no checklist provided

    assert len(objections) == 1
    assert objections[0].clause_ref is None


def test_llm_objection_for_unknown_entry_id_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_llm(
        monkeypatch,
        '[{"entry_id": "invented.section", "objection": "Bad.", "clause_ref": null}]',
    )
    objections = run(examine([_clean_cited_section()], checklist=make_checklist()))
    assert objections == []


def test_llm_non_json_output_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(monkeypatch, "I object to the capital structure section because...")
    objections = run(examine([_clean_cited_section()], checklist=make_checklist()))
    assert objections == []


def test_llm_unavailable_skips_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    async def raising_grounded_complete(
        system: str,
        user: str,
        context_fact_ids: list[str],
        temperature: float = 0.0,
    ) -> LLMResponse:
        raise examiner.LLMUnavailable("no API key configured")

    monkeypatch.setattr("app.llm.client.grounded_complete", raising_grounded_complete)
    section = make_section(text="Pending.", missing_facts=["share_allotments"])
    objections = run(examine([section], checklist=make_checklist()))

    # Deterministic objection survives; no error from the LLM pass.
    assert len(objections) == 1
    assert "share_allotments" in objections[0].objection


def test_offline_stub_providers_do_not_break_examine() -> None:
    # No monkeypatching: the real client's stub providers raise NotImplementedError.
    # examine must still return the deterministic objections (offline-first demo).
    section = make_section(text="Pending.", missing_facts=["share_allotments"])
    objections = run(examine([section], checklist=make_checklist()))
    assert len(objections) == 1
