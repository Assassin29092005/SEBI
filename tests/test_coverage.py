"""Coverage score: auditor content out-of-scope, stubs excluded, gaps never counted covered."""

from app.coverage import CoverageReport, SectionCoverage, score
from app.generate.sections import GeneratedSection
from app.schema.models import (
    Checklist,
    ChecklistEntry,
    ChecklistHeader,
    OutputTarget,
    Role,
    Severity,
)


def _entry(
    entry_id: str,
    section: str,
    role: Role = Role.PROMOTER,
    stub: bool = False,
) -> ChecklistEntry:
    return ChecklistEntry(
        id=entry_id,
        clause_ref="ICDR Sch. VI Part A (as applied by Ch. IX), para 1",
        section=section,
        title=f"Title for {entry_id}",
        description=f"Description for {entry_id}.",
        required_facts=[] if stub else ["some_fact"],
        responsible_role=role,
        severity=Severity.MATERIAL,
        output_targets=[OutputTarget.DRHP],
        stub=stub,
    )


def _generated(entry_id: str, section: str, missing_facts: list[str]) -> GeneratedSection:
    return GeneratedSection(
        entry_id=entry_id,
        section=section,
        text="Generated text.",
        citations=[],
        missing_facts=missing_facts,
    )


def _checklist() -> Checklist:
    return Checklist(
        header=ChecklistHeader(
            regulation="SEBI ICDR Regulations, 2018 — Chapter IX (test fixture)",
            amended_through="2026-03-21",
            schema_version="test",
            reviewed_by_human=True,
        ),
        entries=[
            # Capital Structure: 3 in-scope promoter entries, 1 auditor, 1 stub.
            _entry("capital_structure.share_capital_history", "Capital Structure"),
            _entry("capital_structure.shareholding_pattern", "Capital Structure"),
            _entry("capital_structure.promoter_lock_in", "Capital Structure"),
            _entry(
                "capital_structure.capitalisation_statement",
                "Capital Structure",
                role=Role.AUDITOR,
            ),
            _entry("capital_structure.esop_details", "Capital Structure", stub=True),
            # Financial Information: auditor-only (plus a stub) -> total 0, out_of_scope 1.
            _entry(
                "financial_information.restated_financials",
                "Financial Information",
                role=Role.AUDITOR,
            ),
            _entry(
                "financial_information.other_financial_info",
                "Financial Information",
                stub=True,
            ),
            # Objects of the Issue: one covered promoter entry.
            _entry("objects.use_of_proceeds", "Objects of the Issue"),
            # Other Regulatory Disclosures: all stubs -> omitted from the report.
            _entry("other_regulatory.stub_only", "Other Regulatory Disclosures", stub=True),
        ],
    )


def _sections() -> list[GeneratedSection]:
    return [
        # gap-free -> covered
        _generated("capital_structure.share_capital_history", "Capital Structure", []),
        # generated but with [REQUIRES INPUT] gaps -> NOT covered
        _generated(
            "capital_structure.shareholding_pattern",
            "Capital Structure",
            ["shareholding_pattern_table"],
        ),
        # capital_structure.promoter_lock_in: no generated section -> NOT covered
        # gap-free output for an auditor entry: must still not count toward covered
        _generated("financial_information.restated_financials", "Financial Information", []),
        # gap-free -> covered
        _generated("objects.use_of_proceeds", "Objects of the Issue", []),
    ]


def _by_section(report: CoverageReport) -> dict[str, SectionCoverage]:
    return {s.section: s for s in report.sections}


def test_auditor_entry_counts_only_toward_out_of_scope() -> None:
    report = _by_section(score(_checklist(), _sections()))
    cap = report["Capital Structure"]
    assert cap.out_of_scope == 1
    assert cap.total == 3  # auditor entry not in total, stub excluded

    fin = report["Financial Information"]
    assert fin.out_of_scope == 1
    assert fin.total == 0
    # a gap-free generated section exists for the auditor entry — still never covered
    assert fin.covered == 0


def test_stub_entries_are_excluded_entirely() -> None:
    report = _by_section(score(_checklist(), _sections()))
    cap = report["Capital Structure"]
    # 5 entries in the section; the stub appears in neither total nor out_of_scope
    assert cap.total + cap.out_of_scope == 4
    # an all-stub section produces no row at all
    assert "Other Regulatory Disclosures" not in report


def test_covered_counts_only_gap_free_generated_entries() -> None:
    report = _by_section(score(_checklist(), _sections()))
    cap = report["Capital Structure"]
    # share_capital_history is gap-free; shareholding_pattern has missing_facts;
    # promoter_lock_in was never generated
    assert cap.covered == 1
    assert report["Objects of the Issue"].covered == 1
    assert report["Objects of the Issue"].total == 1


def test_overall_pct() -> None:
    report = score(_checklist(), _sections())
    # covered = 1 (Capital Structure) + 1 (Objects) = 2; total = 3 + 0 + 1 = 4
    assert report.overall_pct == 50.0


def test_overall_pct_zero_when_nothing_in_scope() -> None:
    empty = CoverageReport(sections=[])
    assert empty.overall_pct == 0.0
