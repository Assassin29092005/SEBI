"""Adversarial examiner: deterministic objections offline, sanitised LLM objections online."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from app.facts import Fact, FactStore, Provenance, SourceKind
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
from app.validate.boilerplate import BoilerplateFlag
from app.validate.contradictions import Claim, Contradiction
from app.validate.examiner import Objection, examine

CLAUSE = "ICDR Sch. VI Part A (as applied by Ch. IX), para 9(A)"
UOP_CLAUSE = "ICDR Sch. VI Part A (as applied by Ch. IX), para 9(C)"


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


# --- enriched deterministic pass: cross-check objections from other validators ---


@dataclass
class FakeArithmeticFinding:
    """Duck-typed stand-in for app.validate.arithmetic.ArithmeticFinding."""

    kind: str
    detail: str
    clause_ref: str | None


def make_checklist_with_use_of_proceeds() -> Checklist:
    checklist = make_checklist()
    checklist.entries.append(
        ChecklistEntry(
            id="objects.use_of_proceeds",
            clause_ref=UOP_CLAUSE,
            section="Objects of the Issue",
            title="Use of proceeds",
            description="Object-wise deployment of the issue proceeds.",
            required_facts=["use_of_proceeds"],
            responsible_role=Role.PROMOTER,
            severity=Severity.BLOCKER,
            output_targets=[OutputTarget.DRHP],
        )
    )
    return checklist


def _issue_size_contradiction() -> Contradiction:
    return Contradiction(
        subject="issue_size",
        claims=[
            Claim(
                section_entry_id="capital_structure.share_capital_history",
                kind="number",
                subject="issue_size",
                value="1400000000 paise",
                text_span=(0, 10),
            ),
            Claim(
                section_entry_id="objects.use_of_proceeds",
                kind="number",
                subject="issue_size",
                value="1200000000 paise",
                text_span=(0, 10),
            ),
        ],
    )


def _document_fact(store: FactStore, *, key: str, confidence: float) -> Fact:
    fact = store.add(
        Fact(
            key=key,
            value=140000000000,
            provenance=Provenance(
                kind=SourceKind.DOCUMENT,
                detail="bank_sanction_letter.txt p.1",
                snippet="sanctioned limit",
            ),
            confidence=confidence,
            supplied_by=Role.PROMOTER,
        )
    )
    store.confirm(fact.fact_id)
    return fact


def test_contradiction_raises_reconciliation_objection() -> None:
    contradiction = _issue_size_contradiction()
    objections = run(
        examine(
            [_clean_cited_section()],
            checklist=make_checklist(),
            contradictions=[contradiction],
        )
    )

    assert len(objections) == 1
    obj = objections[0]
    # Routed to the first claim's section, names subject and both values.
    assert obj.entry_id == "capital_structure.share_capital_history"
    assert "issue_size" in obj.objection
    assert "1400000000 paise" in obj.objection
    assert "1200000000 paise" in obj.objection
    assert "reconcile" in obj.objection.lower()
    assert obj.clause_ref == CLAUSE  # from the checklist — never invented
    assert obj.resolved is False


def test_arithmetic_finding_routes_to_use_of_proceeds_when_entry_exists() -> None:
    finding = FakeArithmeticFinding(
        kind="use_of_proceeds_sum",
        detail="object-wise deployment totals Rs 13.5 crore against issue size Rs 14 crore",
        clause_ref=UOP_CLAUSE,
    )
    objections = run(
        examine(
            [_clean_cited_section()],
            checklist=make_checklist_with_use_of_proceeds(),
            arithmetic_findings=[finding],
        )
    )

    assert len(objections) == 1
    obj = objections[0]
    assert obj.entry_id == "objects.use_of_proceeds"
    assert finding.detail in obj.objection
    assert "use_of_proceeds_sum" in obj.objection
    assert obj.clause_ref == UOP_CLAUSE  # arrived on the finding


def test_arithmetic_finding_falls_back_to_first_section_entry() -> None:
    finding = FakeArithmeticFinding(
        kind="total_mismatch", detail="totals disagree", clause_ref=None
    )
    objections = run(
        examine(
            [_clean_cited_section()],
            checklist=make_checklist(),  # no objects.use_of_proceeds entry
            arithmetic_findings=[finding],
        )
    )

    assert len(objections) == 1
    assert objections[0].entry_id == "capital_structure.share_capital_history"
    assert objections[0].clause_ref is None  # none arrived, none invented


def test_boilerplate_flag_quotes_span_and_demands_issuer_specific_language() -> None:
    text = "We are a leading player poised for growth in the agrotech sector."
    section = make_section(
        text=text,
        citations=[Citation(fact_id="fact-1", text_span=(0, len(text)))],
    )
    span = (9, 23)  # "leading player"
    flag = BoilerplateFlag(entry_id=section.entry_id, text_span=span, reason="generic filler")
    objections = run(examine([section], checklist=make_checklist(), boilerplate_flags=[flag]))

    assert len(objections) == 1
    obj = objections[0]
    assert obj.entry_id == section.entry_id
    assert f'"{text[span[0]:span[1]]}"' in obj.objection  # quotes the flagged span
    assert "generic/boilerplate disclosure" in obj.objection
    assert "issuer-specific language" in obj.objection
    assert obj.clause_ref == CLAUSE


def test_low_confidence_document_citation_raises_reverify_objection_once() -> None:
    store = FactStore()
    low = _document_fact(store, key="issue_size_paise", confidence=0.55)
    high = _document_fact(store, key="face_value_paise", confidence=0.95)
    wizard = store.add(
        Fact(
            key="company_name",
            value="Sunrise Agrotech Ltd",
            provenance=Provenance(kind=SourceKind.WIZARD, detail="q_company_name"),
            confidence=0.5,  # low, but wizard-typed — not an extraction
            supplied_by=Role.PROMOTER,
        )
    )
    store.confirm(wizard.fact_id)
    text = "Sunrise Agrotech Ltd raises Rs 14 crore at face value Rs 10 per share."
    section = make_section(
        text=text,
        citations=[
            Citation(fact_id=low.fact_id, text_span=(28, 39)),
            Citation(fact_id=high.fact_id, text_span=(43, 60)),
            Citation(fact_id=wizard.fact_id, text_span=(0, 20)),
            Citation(fact_id=low.fact_id, text_span=(28, 39)),  # cited twice
        ],
    )
    objections = run(examine([section], checklist=make_checklist(), store=store))

    # One objection per distinct low-confidence document fact — not per citation.
    assert len(objections) == 1
    obj = objections[0]
    assert obj.entry_id == section.entry_id
    assert "issue_size_paise" in obj.objection
    assert "Low-confidence extraction cited" in obj.objection
    assert "re-verify against the source document" in obj.objection
    assert obj.clause_ref == CLAUSE


def test_combined_sources_keep_ordering_and_avoid_duplicates() -> None:
    store = FactStore()
    low = _document_fact(store, key="issue_size_paise", confidence=0.4)
    marker = requires_input_marker("share_allotments", "promoter")
    text = f"We are a market leader. The issue size is Rs 14 crore. {marker}"
    section = GeneratedSection(
        entry_id="capital_structure.share_capital_history",
        section="Capital Structure",
        text=text,
        citations=[Citation(fact_id=low.fact_id, text_span=(24, 55))],
        missing_facts=["share_allotments"],
    )
    finding = FakeArithmeticFinding(
        kind="use_of_proceeds_sum", detail="totals disagree", clause_ref=None
    )
    flag = BoilerplateFlag(entry_id=section.entry_id, text_span=(9, 22), reason="generic filler")

    objections = run(
        examine(
            [section],
            checklist=make_checklist(),
            contradictions=[_issue_size_contradiction()],
            boilerplate_flags=[flag],
            arithmetic_findings=[finding],
            store=store,
        )
    )

    # Order: existing deterministic objections first, then contradiction,
    # arithmetic, boilerplate, low-confidence.
    assert len(objections) == 5
    assert "Missing required fact 'share_allotments'" in objections[0].objection
    assert "Contradictory disclosure" in objections[1].objection
    assert "Arithmetic inconsistency" in objections[2].objection
    assert "issuer-specific language" in objections[3].objection
    assert "Low-confidence extraction cited" in objections[4].objection
    # No duplicates.
    assert len({(o.entry_id, o.objection) for o in objections}) == len(objections)


def test_new_kwargs_default_none_pins_old_behaviour() -> None:
    marker = requires_input_marker("share_allotments", "promoter")
    section = make_section(
        text=f"The build-up of capital is as follows. {marker}",
        missing_facts=["share_allotments"],
    )
    baseline = run(examine([section], checklist=make_checklist()))
    explicit = run(
        examine(
            [section],
            checklist=make_checklist(),
            contradictions=None,
            boilerplate_flags=None,
            arithmetic_findings=None,
            store=None,
        )
    )

    assert explicit == baseline
    assert len(baseline) == 1
    assert "share_allotments" in baseline[0].objection
    assert baseline[0].clause_ref == CLAUSE
