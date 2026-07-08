"""End-to-end API smoke + demo-arc tests for the DRHP Studio FastAPI app.

Covers:

* One happy-path hit against each endpoint family (schema, eligibility, wizard,
  facts CRUD, uploads/extract, proposals/accept, litigation, generate/sections,
  contradictions/boilerplate/arithmetic/examiner, coverage, gaps).
* THE DEMO ARC: two contradicting confirmed ``issue_size_paise`` facts →
  ``/api/generate`` → ``/api/validate/contradictions`` must catch the conflict,
  and the enriched examiner must raise a reviewer objection over the same
  contradiction.
* CERTIFICATION LOCK: ``/api/review/export`` refuses (409) with a non-empty
  blocker list; iterating blockers through ``draft → reviewed → certified``
  unlocks the export; downloadable ``.docx`` files come back with the right
  content-type and non-empty bodies. ``GET /api/export/bundle`` is gated by the
  same lock and, once unlocked, streams a well-formed ZIP with the full audit
  trail.
* PERSISTENCE: with ``persist_session`` on, mutations snapshot to disk and a
  simulated restart (fresh state + the module's restore path) revives facts —
  confirmation status included — plus sections and review states.

Every test resets ``app.main.state`` via the ``fresh_app`` fixture — cases must
not leak facts, review states, or generated sections into each other. The
fixture also points session persistence away from the real ``data/session/``
(and disables it) so the suite never writes or deletes a live demo session.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.assemble.bundle import BUNDLE_MEMBERS
from app.config import settings
from app.review.workflow import SectionState
from app.schema.models import Severity


DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def fresh_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """Reset the module state before every test and yield a TestClient.

    Persistence is disabled AND ``session_dir`` is redirected into ``tmp_path``:
    ``reset_state()`` unconditionally clears the snapshot at
    ``settings.session_dir``, so without the redirect the suite would delete a
    developer's live ``data/session/`` snapshot. With it, tests can never read,
    write, or delete the real session directory.
    """
    monkeypatch.setattr(settings, "persist_session", False)
    monkeypatch.setattr(settings, "session_dir", tmp_path / "session")
    main_module.reset_state()
    with TestClient(main_module.app) as client:
        yield client
    main_module.reset_state()


def _blocker_entry_ids() -> list[str]:
    return [e.id for e in main_module.checklist.entries if e.severity == Severity.BLOCKER]


def _seed_fact(
    client: TestClient,
    key: str,
    value: Any,
    detail: str,
    kind: str = "wizard",
    confirmed: bool = True,
) -> str:
    """Post a fact and (optionally) confirm it — return its fact_id."""
    body = {
        "key": key,
        "value": value,
        "provenance": {"kind": kind, "detail": detail},
        "supplied_by": "promoter",
    }
    resp = client.post("/api/facts", json=body)
    assert resp.status_code == 200, resp.text
    fact_id = resp.json()["fact_id"]
    if confirmed:
        resp = client.post(f"/api/facts/{fact_id}/confirm")
        assert resp.status_code == 200, resp.text
    return fact_id


# --------------------------------------------------------------------------
# Simple smoke tests, one per endpoint family
# --------------------------------------------------------------------------


def test_health_and_schema(fresh_app: TestClient) -> None:
    resp = fresh_app.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["schema_version"], "checklist must expose a schema_version"

    schema = fresh_app.get("/api/schema").json()
    assert schema["header"]["schema_version"] == body["schema_version"]
    assert isinstance(schema["entries"], list) and schema["entries"]


def test_eligibility_pass(fresh_app: TestClient) -> None:
    payload = {
        "post_issue_paid_up_capital_paise": 15 * 10**9,   # ₹15 crore, well within cap
        "operating_profit_years": 3,
        "min_operating_profit_paise": 2 * 10**9,           # ₹2 crore
        "is_debarred_by_sebi": False,
        "promoter_director_of_debarred_company": False,
        "is_wilful_defaulter_or_fraudulent_borrower": False,
        "is_fugitive_economic_offender": False,
        "has_outstanding_convertibles": False,
        "promoter_change_within_1yr": False,
        "ofs_pct_of_issue": 10.0,
        "promoter_shares_demat": True,
        "partly_paid_shares_outstanding": False,
    }
    resp = fresh_app.post("/api/eligibility", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "pass"
    assert body["items"] == []


def test_wizard_questions_en_and_hi(fresh_app: TestClient) -> None:
    en = fresh_app.get("/api/wizard/questions?lang=en")
    hi = fresh_app.get("/api/wizard/questions?lang=hi")
    assert en.status_code == 200 and hi.status_code == 200
    en_questions = en.json()
    hi_questions = hi.json()
    assert en_questions and hi_questions
    # Every question in either language must carry a clause_ref (no orphan questions).
    for q in en_questions + hi_questions:
        assert q["clause_ref"].strip()
    # Same fact-key coverage across languages — copy differs, structure doesn't.
    assert {q["fact_key"] for q in en_questions} == {q["fact_key"] for q in hi_questions}


def test_facts_crud_add_confirm_correct(fresh_app: TestClient) -> None:
    fact_id = _seed_fact(
        fresh_app,
        key="issuer_identity",
        value="Sunrise Agrotech Ltd",
        detail="wizard:issuer_identity",
        confirmed=False,
    )
    all_facts = fresh_app.get("/api/facts").json()
    assert any(f["fact_id"] == fact_id for f in all_facts)
    assert any(not f["confirmed"] for f in all_facts)

    confirmed = fresh_app.post(f"/api/facts/{fact_id}/confirm").json()
    assert confirmed["confirmed"] is True

    correction = {
        "value": "Sunrise Agrotech Limited",
        "provenance": {"kind": "wizard", "detail": "wizard:issuer_identity (typo fix)"},
    }
    corrected = fresh_app.post(f"/api/facts/{fact_id}/correct", json=correction).json()
    assert corrected["value"] == "Sunrise Agrotech Limited"
    assert corrected["provenance"]["supersedes"] == fact_id


def test_uploads_extract_txt_payload(fresh_app: TestClient) -> None:
    body = b"Issue Size: Rs 14.00 crore\nSme Exchange: NSE Emerge\n"
    resp = fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("prospectus.txt", body, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    proposals = resp.json()
    fact_keys = {p["fact_key"] for p in proposals}
    assert "issue_size_paise" in fact_keys
    issue = next(p for p in proposals if p["fact_key"] == "issue_size_paise")
    assert issue["value"] == 14 * 10**9  # ₹14 cr → paise


def test_proposals_accept_creates_unconfirmed_fact(fresh_app: TestClient) -> None:
    proposal = {
        "fact_key": "issue_size_paise",
        "value": 14 * 10**9,
        "source_file": "prospectus.txt",
        "page": 1,
        "snippet": "Issue Size: Rs 14.00 crore",
        "confidence": 0.9,
    }
    resp = fresh_app.post("/api/proposals/accept", json=proposal)
    assert resp.status_code == 200
    fact = resp.json()
    assert fact["key"] == "issue_size_paise"
    assert fact["confirmed"] is False  # accept ≠ confirm
    assert fact["provenance"]["kind"] == "document"


def test_litigation_returns_records_for_demo_entity(fresh_app: TestClient) -> None:
    resp = fresh_app.get("/api/litigation", params={"entity": "Sunrise Agrotech Ltd"})
    assert resp.status_code == 200
    records = resp.json()
    assert records, "MockLitigationConnector should return demo records for Sunrise Agrotech"
    for rec in records:
        assert rec["case_number"] and rec["forum"]


def test_generate_caches_sections_and_get_sections_returns_them(fresh_app: TestClient) -> None:
    # Empty fact store still yields sections (all-missing gap paragraphs).
    empty = fresh_app.get("/api/sections").json()
    assert empty == []
    generated = fresh_app.post("/api/generate").json()
    assert generated, "generate_all should produce at least one section"
    cached = fresh_app.get("/api/sections").json()
    assert cached == generated


def test_validate_endpoints_run_over_cached_sections(fresh_app: TestClient) -> None:
    fresh_app.post("/api/generate")
    for path in (
        "/api/validate/contradictions",
        "/api/validate/boilerplate",
        "/api/validate/examiner",
    ):
        resp = fresh_app.get(path)
        assert resp.status_code == 200, f"{path}: {resp.text}"
        assert isinstance(resp.json(), list)


def test_validate_arithmetic_returns_findings_list(fresh_app: TestClient) -> None:
    """Shape only: an empty store may legitimately yield a missing_inputs finding."""
    resp = fresh_app.get("/api/validate/arithmetic")
    assert resp.status_code == 200, resp.text
    findings = resp.json()
    assert isinstance(findings, list)
    for finding in findings:
        assert {"kind", "detail", "severity"} <= set(finding)


def test_coverage_and_gaps(fresh_app: TestClient) -> None:
    fresh_app.post("/api/generate")
    cov = fresh_app.get("/api/coverage").json()
    assert "sections" in cov and isinstance(cov["sections"], list)
    gaps = fresh_app.get("/api/gaps").json()
    assert "gaps" in gaps and isinstance(gaps["gaps"], list)


# --------------------------------------------------------------------------
# THE DEMO ARC: planted-contradiction detection
# --------------------------------------------------------------------------


def test_demo_arc_planted_contradiction_is_caught(fresh_app: TestClient) -> None:
    """Two confirmed ``issue_size_paise`` facts must show up as a contradiction."""
    # Wizard says ₹12.5 crore (= 12_50_00_00_000 paise), document says ₹14 crore.
    _seed_fact(
        fresh_app,
        key="issue_size_paise",
        value=125 * 10**8,   # 12.5 crore in paise
        detail="wizard:issue_size",
    )
    _seed_fact(
        fresh_app,
        key="issue_size_paise",
        value=14 * 10**9,    # 14 crore in paise
        detail="prospectus.txt p.1",
        kind="document",
    )
    fresh_app.post("/api/generate")
    contradictions = fresh_app.get("/api/validate/contradictions").json()
    assert contradictions, "planted issue_size contradiction was not detected"
    subjects = {c["subject"] for c in contradictions}
    assert any("issue_size" in s for s in subjects), (
        f"expected an issue_size contradiction, got subjects={subjects}"
    )


def test_enriched_examiner_objects_to_planted_contradiction(fresh_app: TestClient) -> None:
    """The examiner now consumes the contradiction check's output as objections."""
    _seed_fact(
        fresh_app,
        key="issue_size_paise",
        value=125 * 10**8,   # wizard: ₹12.5 crore in paise
        detail="wizard:issue_size",
    )
    _seed_fact(
        fresh_app,
        key="issue_size_paise",
        value=14 * 10**9,    # document: ₹14 crore in paise
        detail="prospectus.txt p.1",
        kind="document",
    )
    fresh_app.post("/api/generate")
    resp = fresh_app.get("/api/validate/examiner")
    assert resp.status_code == 200, resp.text
    objections = resp.json()
    assert objections, "examiner returned no objections over a contradicted draft"
    texts = [o["objection"] for o in objections]
    assert any("Contradictory" in t and "issue_size" in t for t in texts), (
        f"expected a contradiction objection mentioning issue_size, got: {texts}"
    )


# --------------------------------------------------------------------------
# Certification lock: export blocked → advance → export succeeds → files served
# --------------------------------------------------------------------------


def test_certification_lock_blocks_then_unlocks_and_files_are_downloadable(
    fresh_app: TestClient,
) -> None:
    # Ensure we have generated sections cached for assembly to embed.
    fresh_app.post("/api/generate")

    # Initial export must be blocked with a non-empty blocker list.
    blocked = fresh_app.post("/api/review/export")
    assert blocked.status_code == 409, blocked.text
    payload = blocked.json()["detail"]
    assert isinstance(payload, dict) and payload.get("blocked_by")
    blockers = payload["blocked_by"]
    expected_blockers = set(_blocker_entry_ids())
    assert set(blockers) == expected_blockers

    # Advance every blocker through draft → reviewed → certified.
    for entry_id in blockers:
        for target_state in (SectionState.REVIEWED, SectionState.CERTIFIED):
            resp = fresh_app.post(
                f"/api/review/{entry_id}/advance", json={"to": target_state.value}
            )
            assert resp.status_code == 200, resp.text

    # Export again — should now succeed and return download URLs.
    unlocked = fresh_app.post("/api/review/export")
    assert unlocked.status_code == 200, unlocked.text
    urls = unlocked.json()
    assert urls == {"drhp": "/api/assemble/drhp", "abridged": "/api/assemble/abridged"}

    for target in ("drhp", "abridged"):
        resp = fresh_app.get(f"/api/assemble/{target}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(DOCX_MEDIA_TYPE)
        assert len(resp.content) > 0, f"assembled {target} .docx was empty"


def test_illegal_review_advance_returns_409(fresh_app: TestClient) -> None:
    """Skipping a state (draft → certified) must surface as HTTP 409."""
    blockers = _blocker_entry_ids()
    assert blockers, "checklist must contain at least one blocker for this test"
    resp = fresh_app.post(
        f"/api/review/{blockers[0]}/advance", json={"to": SectionState.CERTIFIED.value}
    )
    assert resp.status_code == 409


def test_review_edit_records_audit_trail(fresh_app: TestClient) -> None:
    entry_id = _blocker_entry_ids()[0]
    edit = {
        "entry_id": entry_id,
        "editor": "banker@example.com",
        "before": "old text",
        "after": "new text",
    }
    resp = fresh_app.post("/api/review/edit", json=edit)
    assert resp.status_code == 200
    state_view = fresh_app.get("/api/review/state").json()
    assert any(e["entry_id"] == entry_id for e in state_view["audit_trail"])


# --------------------------------------------------------------------------
# Exchange-ready bundle: same certification lock, then a well-formed ZIP
# --------------------------------------------------------------------------


def test_export_bundle_locked_then_unlocked_zip(fresh_app: TestClient) -> None:
    fresh_app.post("/api/generate")

    # Same certification lock as /api/review/export: 409 + blocker list.
    blocked = fresh_app.get("/api/export/bundle")
    assert blocked.status_code == 409, blocked.text
    payload = blocked.json()["detail"]
    assert isinstance(payload, dict) and payload.get("blocked_by")
    assert set(payload["blocked_by"]) == set(_blocker_entry_ids())

    # Certify every blocker (draft → reviewed → certified).
    for entry_id in _blocker_entry_ids():
        for target_state in (SectionState.REVIEWED, SectionState.CERTIFIED):
            resp = fresh_app.post(
                f"/api/review/{entry_id}/advance", json={"to": target_state.value}
            )
            assert resp.status_code == 200, resp.text

    resp = fresh_app.get("/api/export/bundle")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/zip")
    assert "drhp_studio_package.zip" in resp.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        assert set(archive.namelist()) == set(BUNDLE_MEMBERS)
        assert archive.testzip() is None  # every member readable, none corrupt
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        assert manifest["schema_version"] == main_module.checklist.header.schema_version


# --------------------------------------------------------------------------
# Session persistence: mutations snapshot to disk; a restart revives them
# --------------------------------------------------------------------------


def test_session_persists_across_simulated_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """add → confirm → generate → advance, 'restart', snapshot rehydrates it all.

    Deliberately not using ``fresh_app`` (which disables persistence): this test
    turns ``persist_session`` on against a tmp ``session_dir`` and exercises the
    module's real boot-time restore path.
    """
    monkeypatch.setattr(settings, "persist_session", True)
    monkeypatch.setattr(settings, "session_dir", tmp_path / "session")
    main_module.reset_state()
    try:
        with TestClient(main_module.app) as client:
            confirmed_id = _seed_fact(
                client,
                key="issuer_name",
                value="Sunrise Agrotech Ltd",
                detail="wizard:issuer_name",
            )
            pending_id = _seed_fact(
                client,
                key="board_size",
                value=6,
                detail="wizard:board_size",
                confirmed=False,
            )
            client.post("/api/generate")
            blocker = _blocker_entry_ids()[0]
            resp = client.post(
                f"/api/review/{blocker}/advance", json={"to": SectionState.REVIEWED.value}
            )
            assert resp.status_code == 200, resp.text

        section_ids_before = [s.entry_id for s in main_module.state.generated_sections]
        assert section_ids_before, "generate must have cached sections before the restart"

        # Simulated restart: brand-new empty in-memory state...
        main_module.state = main_module.create_state()
        assert main_module.state.fact_store.all_facts() == []
        # ...rehydrated by the module's boot-time restore path (load + rebuild).
        main_module.restore_persisted_state(main_module.state)

        store = main_module.state.fact_store
        assert store.get(confirmed_id).confirmed is True  # survives WITH confirmation
        assert store.get(confirmed_id).value == "Sunrise Agrotech Ltd"
        assert store.get(pending_id).confirmed is False   # unconfirmed proposals survive too
        assert [s.entry_id for s in main_module.state.generated_sections] == section_ids_before
        assert main_module.state.review_state.states[blocker] == SectionState.REVIEWED

        # The revived store serves the API exactly like the original.
        with TestClient(main_module.app) as client:
            facts = client.get("/api/facts").json()
        assert any(f["fact_id"] == confirmed_id and f["confirmed"] for f in facts)
    finally:
        # Clean slate for later tests; clears the tmp snapshot (session_dir is
        # still monkeypatched here — the real data/session/ is never touched).
        main_module.reset_state()
