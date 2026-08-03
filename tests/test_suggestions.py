"""Auto-suggested fixes (app.validate.suggestions): concrete, computed
remediation over validator output — never a new fact or invented number.

Every scenario here was hand-verified against a standalone script before
being written up as a formal test.
"""

from __future__ import annotations

from app.facts import Fact, FactStore, Provenance, SourceKind
from app.generate.sections import Citation, GeneratedSection
from app.schema.models import Role
from app.validate.arithmetic import ArithmeticFinding
from app.validate.boilerplate import BoilerplateFlag
from app.validate.suggestions import compute_suggested_fixes

ENTRY_ID = "objects.use_of_proceeds"


def _finding(kind: str, expected: int, actual: int) -> ArithmeticFinding:
    return ArithmeticFinding(
        kind=kind,  # type: ignore[arg-type]
        detail="...",
        expected_paise=expected,
        actual_paise=actual,
        severity="blocker",
        clause_ref="x",
    )


# --------------------------------------------------------------------------
# Arithmetic: concrete remediation amounts, computed from expected/actual
# --------------------------------------------------------------------------


def test_objects_overallocated_suggests_the_exact_overage() -> None:
    finding = _finding("objects_overallocated", expected=1_400_000_000_00, actual=1_500_000_000_00)
    suggestions = compute_suggested_fixes([], FactStore(), [finding], [])
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.category == "arithmetic"
    assert s.entry_id == ENTRY_ID
    assert str(abs(finding.expected_paise - finding.actual_paise)) in s.message


def test_unallocated_proceeds_suggests_the_exact_residual() -> None:
    finding = _finding("unallocated_proceeds", expected=1_400_000_000_00, actual=1_200_000_000_00)
    suggestions = compute_suggested_fixes([], FactStore(), [finding], [])
    assert len(suggestions) == 1
    assert str(abs(finding.expected_paise - finding.actual_paise)) in suggestions[0].message
    assert "allocate" in suggestions[0].message.lower()


def test_gcp_cap_breach_suggests_the_cap_itself() -> None:
    finding = _finding("gcp_cap_breach", expected=210_000_000_00, actual=250_000_000_00)
    suggestions = compute_suggested_fixes([], FactStore(), [finding], [])
    assert len(suggestions) == 1
    assert str(finding.expected_paise) in suggestions[0].message  # the cap to reduce to


def test_finding_missing_expected_or_actual_yields_no_suggestion() -> None:
    finding = ArithmeticFinding(
        kind="objects_overallocated", detail="x", expected_paise=None, actual_paise=None,
        severity="blocker", clause_ref="x",
    )
    assert compute_suggested_fixes([], FactStore(), [finding], []) == []


def test_multiple_arithmetic_findings_each_get_a_suggestion() -> None:
    findings = [
        _finding("objects_overallocated", 100, 200),
        _finding("gcp_cap_breach", 50, 90),
    ]
    suggestions = compute_suggested_fixes([], FactStore(), findings, [])
    assert len(suggestions) == 2
    assert all(s.category == "arithmetic" for s in suggestions)


# --------------------------------------------------------------------------
# Low-confidence extraction: jump-to-source target, same detection as the
# examiner's own low-confidence pass
# --------------------------------------------------------------------------


def _document_fact(store: FactStore, *, confidence: float, document_id: str | None = "doc-1") -> Fact:
    fact = store.add(
        Fact(
            key="issue_size_paise",
            value=14_000_000_000,
            provenance=Provenance(
                kind=SourceKind.DOCUMENT,
                detail="bank_sanction_letter.pdf p.2",
                snippet="Issue Size: Rs 14.00 crore",
                document_id=document_id,
                page=2,
                source_file="bank_sanction_letter.pdf",
            ),
            confidence=confidence,
            supplied_by=Role.PROMOTER,
        )
    )
    store.confirm(fact.fact_id)
    return fact


def _section_citing(fact: Fact) -> GeneratedSection:
    return GeneratedSection(
        entry_id=ENTRY_ID,
        section="Objects of the Issue",
        text="Issue size: Rs 14.00 crore.",
        citations=[Citation(fact_id=fact.fact_id, text_span=(0, 10))],
        missing_facts=[],
    )


def test_low_confidence_document_fact_carries_jump_to_source() -> None:
    store = FactStore()
    fact = _document_fact(store, confidence=0.55)
    suggestions = compute_suggested_fixes([_section_citing(fact)], store, [], [])
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.category == "low_confidence_extraction"
    assert s.fact_id == fact.fact_id
    assert s.document_id == "doc-1"
    assert s.page == 2
    assert s.entry_id == ENTRY_ID


def test_high_confidence_document_fact_yields_no_suggestion() -> None:
    store = FactStore()
    fact = _document_fact(store, confidence=0.95)
    assert compute_suggested_fixes([_section_citing(fact)], store, [], []) == []


def test_wizard_sourced_fact_never_suggests_reverification() -> None:
    store = FactStore()
    fact = store.add(
        Fact(
            key="issue_size_paise",
            value=14_000_000_000,
            provenance=Provenance(kind=SourceKind.WIZARD, detail="q:issue_size"),
            confidence=0.4,  # low, but a promoter typing an answer isn't "extraction"
            supplied_by=Role.PROMOTER,
        )
    )
    store.confirm(fact.fact_id)
    assert compute_suggested_fixes([_section_citing(fact)], store, [], []) == []


def test_low_confidence_fact_without_document_id_still_suggests_but_no_jump_target() -> None:
    store = FactStore()
    fact = _document_fact(store, confidence=0.5, document_id=None)
    suggestions = compute_suggested_fixes([_section_citing(fact)], store, [], [])
    assert len(suggestions) == 1
    assert suggestions[0].document_id is None
    assert suggestions[0].fact_id == fact.fact_id


def test_same_fact_cited_twice_yields_one_suggestion_not_two() -> None:
    store = FactStore()
    fact = _document_fact(store, confidence=0.5)
    section = GeneratedSection(
        entry_id=ENTRY_ID,
        section="Objects of the Issue",
        text="Rs 14.00 crore, Rs 14.00 crore again.",
        citations=[
            Citation(fact_id=fact.fact_id, text_span=(0, 10)),
            Citation(fact_id=fact.fact_id, text_span=(20, 30)),
        ],
        missing_facts=[],
    )
    suggestions = compute_suggested_fixes([section], store, [], [])
    assert len(suggestions) == 1


def test_dangling_citation_to_a_missing_fact_does_not_crash() -> None:
    section = GeneratedSection(
        entry_id=ENTRY_ID,
        section="Objects of the Issue",
        text="x",
        citations=[Citation(fact_id="nonexistent-fact-id", text_span=(0, 1))],
        missing_facts=[],
    )
    assert compute_suggested_fixes([section], FactStore(), [], []) == []


# --------------------------------------------------------------------------
# Boilerplate: one suggestion per flagged section, pointing at the
# iterative examiner
# --------------------------------------------------------------------------


def test_boilerplate_flags_dedupe_to_one_suggestion_per_section() -> None:
    flags = [
        BoilerplateFlag(entry_id="business.overview", text_span=(0, 10), reason="generic filler"),
        BoilerplateFlag(entry_id="business.overview", text_span=(20, 30), reason="generic filler"),
        BoilerplateFlag(entry_id="general.cover_pages", text_span=(0, 5), reason="near-duplicate"),
    ]
    suggestions = compute_suggested_fixes([], FactStore(), [], flags)
    assert len(suggestions) == 2
    assert {s.entry_id for s in suggestions} == {"business.overview", "general.cover_pages"}
    assert all(s.category == "boilerplate" for s in suggestions)
    assert all("iterative examiner" in s.message for s in suggestions)


def test_no_boilerplate_flags_yields_no_suggestions() -> None:
    assert compute_suggested_fixes([], FactStore(), [], []) == []


# --------------------------------------------------------------------------
# Combined ordering
# --------------------------------------------------------------------------


def test_suggestions_come_back_in_a_fixed_category_order() -> None:
    arithmetic = [_finding("gcp_cap_breach", 100, 200)]
    flags = [BoilerplateFlag(entry_id="business.overview", text_span=(0, 1), reason="generic filler")]
    store = FactStore()
    fact = _document_fact(store, confidence=0.5)
    suggestions = compute_suggested_fixes([_section_citing(fact)], store, arithmetic, flags)
    assert [s.category for s in suggestions] == [
        "arithmetic",
        "low_confidence_extraction",
        "boilerplate",
    ]
