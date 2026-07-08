"""Bundle tests: the exchange-ready ZIP round-trips with a complete audit trail.

Small synthetic pipeline state only — offline, no LLM, no real schema, and
fake .docx bytes (build_bundle copies them; it never assembles documents).
"""

import json
import zipfile
from pathlib import Path

from app.assemble.bundle import BUNDLE_NOTE, build_bundle
from app.coverage import CoverageReport, SectionCoverage
from app.facts import Fact, FactStore, Provenance, SourceKind
from app.generate.sections import Citation, GeneratedSection
from app.review.workflow import BankerEdit, ReviewState
from app.schema.models import (
    Checklist,
    ChecklistEntry,
    ChecklistHeader,
    OutputTarget,
    Role,
    Severity,
)
from app.validate.gaps import GapReport

ENTRY_ID = "general.company_overview"
SCHEMA_VERSION = "test-bundle-0.0.1"
DRHP_BYTES = b"fake drhp docx bytes"
ABRIDGED_BYTES = b"fake abridged docx bytes"

EXPECTED_MEMBERS = {
    "drhp.docx",
    "abridged.docx",
    "gap_report.json",
    "contradictions.json",
    "coverage.json",
    "examiner_objections.json",
    "arithmetic_findings.json",
    "generated_sections.json",
    "facts_with_provenance.json",
    "review_state.json",
    "manifest.json",
}


def _checklist() -> Checklist:
    header = ChecklistHeader(
        regulation="SEBI ICDR Regulations, 2018 — Chapter IX",
        amended_through="2026-03-21",
        schema_version=SCHEMA_VERSION,
        reviewed_by_human=True,
    )
    entry = ChecklistEntry(
        id=ENTRY_ID,
        clause_ref="ICDR Sch. VI Part A (as applied by Ch. IX), para (6)",
        section="General Information",
        title="Company overview",
        description="Overview of the issuer's business and incorporation history.",
        required_facts=["company_name"],
        responsible_role=Role.PROMOTER,
        severity=Severity.BLOCKER,
        output_targets=[OutputTarget.DRHP, OutputTarget.ABRIDGED],
    )
    return Checklist(header=header, entries=[entry])


def _store() -> tuple[FactStore, Fact, Fact, Fact]:
    """Store with a confirmed fact, an unconfirmed fact, and a superseded version."""
    store = FactStore()
    confirmed = store.add(
        Fact(
            key="company_name",
            value="Sunrise Agrotech Ltd",
            provenance=Provenance(kind=SourceKind.WIZARD, detail="q_company_name"),
            supplied_by=Role.PROMOTER,
        )
    )
    confirmed = store.confirm(confirmed.fact_id)
    unconfirmed = store.add(
        Fact(
            key="issue_size_paise",
            value=24_50_00_000_00,  # ₹24.50 crore, integer paise
            provenance=Provenance(
                kind=SourceKind.DOCUMENT,
                detail="bank_sanction_letter.txt p.1",
                snippet="sanction of Rs. 24,50,00,000",
            ),
            confidence=0.8,
            supplied_by=Role.PROMOTER,
        )
    )
    # A correction supersedes the unconfirmed proposal — the audit trail must
    # retain both versions.
    correction = store.correct(
        unconfirmed.fact_id,
        25_00_00_000_00,
        Provenance(kind=SourceKind.WIZARD, detail="q_issue_size (corrected)"),
    )
    return store, confirmed, unconfirmed, correction


def _section(confirmed: Fact) -> GeneratedSection:
    text = "Company name: Sunrise Agrotech Ltd (source: q_company_name)."
    return GeneratedSection(
        entry_id=ENTRY_ID,
        section="General Information",
        text=text,
        citations=[Citation(fact_id=confirmed.fact_id, text_span=(0, len(text)))],
        missing_facts=[],
    )


def _review_state() -> ReviewState:
    review = ReviewState()
    review.record_edit(
        BankerEdit(
            entry_id=ENTRY_ID,
            editor="lm@examplebank.test",
            before="Company overview draft v1",
            after="Company overview draft v2",
        )
    )
    return review


def _build(tmp_path: Path, out_name: str = "bundle.zip") -> tuple[Path, dict[str, str]]:
    """Build a bundle from the synthetic state; returns (zip path, fact ids)."""
    store, confirmed, unconfirmed, correction = _store()
    drhp_src = tmp_path / "drhp_src.docx"
    abridged_src = tmp_path / "abridged_src.docx"
    drhp_src.write_bytes(DRHP_BYTES)
    abridged_src.write_bytes(ABRIDGED_BYTES)
    out = build_bundle(
        checklist=_checklist(),
        sections=[_section(confirmed)],
        store=store,
        review_state=_review_state(),
        gaps=GapReport(gaps=[]),
        contradictions=[],
        coverage=CoverageReport(
            sections=[
                SectionCoverage(section="General Information", covered=1, total=1, out_of_scope=0)
            ]
        ),
        objections=[],
        arithmetic=[{"entry_id": ENTRY_ID, "check": "use_of_proceeds_total", "ok": True}],
        drhp_path=drhp_src,
        abridged_path=abridged_src,
        out_path=tmp_path / out_name,
    )
    fact_ids = {
        "confirmed": confirmed.fact_id,
        "unconfirmed": unconfirmed.fact_id,
        "correction": correction.fact_id,
    }
    return out, fact_ids


def _member_json(bundle_path: Path, member: str) -> dict:
    with zipfile.ZipFile(bundle_path) as archive:
        return json.loads(archive.read(member).decode("utf-8"))


def test_all_expected_members_present(tmp_path: Path) -> None:
    out, _ = _build(tmp_path)
    assert out.exists()
    with zipfile.ZipFile(out) as archive:
        assert set(archive.namelist()) == EXPECTED_MEMBERS
        assert archive.testzip() is None  # every member readable, none corrupt


def test_docx_members_copied_byte_for_byte(tmp_path: Path) -> None:
    out, _ = _build(tmp_path)
    with zipfile.ZipFile(out) as archive:
        assert archive.read("drhp.docx") == DRHP_BYTES
        assert archive.read("abridged.docx") == ABRIDGED_BYTES


def test_manifest_pins_schema_version_and_note(tmp_path: Path) -> None:
    out, _ = _build(tmp_path)
    manifest = _member_json(out, "manifest.json")
    assert manifest["generated_by"] == "DRHP Studio"
    assert manifest["regulation"] == "SEBI ICDR Regulations, 2018 — Chapter IX"
    assert manifest["amended_through"] == "2026-03-21"
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["reviewed_by_human"] is True
    assert manifest["note"] == BUNDLE_NOTE
    assert "merchant banker certification" in manifest["note"]
    with zipfile.ZipFile(out) as archive:
        assert set(manifest["contents"]) == set(archive.namelist())


def test_facts_json_is_the_complete_audit_trail(tmp_path: Path) -> None:
    """Unconfirmed and superseded facts are in the bundle — nothing hidden."""
    out, fact_ids = _build(tmp_path)
    items = _member_json(out, "facts_with_provenance.json")["items"]
    by_id = {item["fact_id"]: item for item in items}
    assert set(by_id) == set(fact_ids.values())  # all three versions present

    unconfirmed = by_id[fact_ids["unconfirmed"]]
    assert unconfirmed["confirmed"] is False
    assert unconfirmed["provenance"]["detail"] == "bank_sanction_letter.txt p.1"

    correction = by_id[fact_ids["correction"]]
    assert correction["provenance"]["supersedes"] == fact_ids["unconfirmed"]

    assert by_id[fact_ids["confirmed"]]["confirmed"] is True


def test_review_state_carries_states_and_audit_trail(tmp_path: Path) -> None:
    out, _ = _build(tmp_path)
    review = _member_json(out, "review_state.json")
    assert review["states"][ENTRY_ID] == "draft"  # banker edit drops back to draft
    assert len(review["audit_trail"]) == 1
    edit = review["audit_trail"][0]
    assert edit["editor"] == "lm@examplebank.test"
    assert edit["before"] == "Company overview draft v1"


def test_report_payloads_wrapped_as_items(tmp_path: Path) -> None:
    out, fact_ids = _build(tmp_path)
    assert _member_json(out, "contradictions.json") == {"items": []}
    assert _member_json(out, "examiner_objections.json") == {"items": []}
    arithmetic = _member_json(out, "arithmetic_findings.json")["items"]
    assert arithmetic[0]["check"] == "use_of_proceeds_total"
    sections = _member_json(out, "generated_sections.json")["items"]
    assert sections[0]["entry_id"] == ENTRY_ID
    assert sections[0]["citations"][0]["fact_id"] == fact_ids["confirmed"]
    gaps = _member_json(out, "gap_report.json")
    assert gaps == {"gaps": []}
    coverage = _member_json(out, "coverage.json")
    assert coverage["sections"][0]["covered"] == 1


def test_no_tmp_leftovers(tmp_path: Path) -> None:
    _build(tmp_path)
    assert not list(tmp_path.rglob("*.tmp"))


def test_deterministic_member_set(tmp_path: Path) -> None:
    first, _ = _build(tmp_path, out_name="bundle_a.zip")
    second, _ = _build(tmp_path, out_name="bundle_b.zip")
    with zipfile.ZipFile(first) as a, zipfile.ZipFile(second) as b:
        assert a.namelist() == b.namelist()  # same members, same order, every build
