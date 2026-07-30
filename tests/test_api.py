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

Every endpoint now requires a bearer token (see ``app.auth``): ``fresh_app``
registers a fresh promoter account per test and authenticates the client as
that promoter by default; ``banker_headers`` registers a second account for
the handful of actions (certification advance/edit) that require the banker
role. A dedicated ``test_auth.py``-style block near the bottom of this file
covers 401/403 enforcement itself.
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
from app.auth.store import reset_user_store
from app.config import settings
from app.review.workflow import SectionState
from app.schema.models import Severity


DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

TEST_BANKER_INVITE = "test-banker-invite"
TEST_AUDITOR_INVITE = "test-auditor-invite"
TEST_PASSWORD = "Correct-Horse-Battery-Staple-1"  # noqa: S105 — test fixture, not a real secret


def _register(client: TestClient, *, email: str, name: str, role: str, invite_code: str = "") -> str:
    """Register a fresh account and return its bearer token."""
    body: dict[str, Any] = {"email": email, "name": name, "password": TEST_PASSWORD, "role": role}
    if invite_code:
        body["invite_code"] = invite_code
    resp = client.post("/api/auth/register", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def fresh_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """Reset the module state before every test and yield a promoter-authenticated TestClient.

    Persistence is disabled AND ``session_dir``/``auth_dir``/``uploads_dir``
    are redirected into ``tmp_path``: ``reset_state()`` unconditionally
    clears the snapshot at ``settings.session_dir`` (and the uploads vault at
    ``settings.uploads_dir``), so without the redirect the suite would delete
    a developer's live ``data/session/``/``data/uploads/`` contents (and, for
    auth, the real registered-users file). With the redirect, tests can never
    read, write, or delete real on-disk state.
    """
    monkeypatch.setattr(settings, "persist_session", False)
    monkeypatch.setattr(settings, "session_dir", tmp_path / "session")
    monkeypatch.setattr(settings, "auth_dir", tmp_path / "auth")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "banker_invite_code", TEST_BANKER_INVITE)
    monkeypatch.setattr(settings, "auditor_invite_code", TEST_AUDITOR_INVITE)
    main_module.reset_state()
    reset_user_store()
    with TestClient(main_module.app) as client:
        token = _register(client, email="promoter@test.example", name="Test Promoter", role="promoter")
        client.headers["Authorization"] = f"Bearer {token}"
        yield client
    main_module.reset_state()
    reset_user_store()


@pytest.fixture()
def banker_headers(fresh_app: TestClient) -> dict[str, str]:
    """A second, banker-role account's auth header — for certification-only actions."""
    token = _register(
        fresh_app,
        email="banker@test.example",
        name="Test Banker",
        role="banker",
        invite_code=TEST_BANKER_INVITE,
    )
    return {"Authorization": f"Bearer {token}"}


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


def test_upload_is_archived_and_downloadable(fresh_app: TestClient) -> None:
    """The encrypted document vault: extraction archives the original, and it
    comes back byte-for-byte via the list/download endpoints."""
    body = b"Issue Size: Rs 14.00 crore\nSme Exchange: NSE Emerge\n"
    extract_resp = fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("prospectus.txt", body, "text/plain")},
    )
    assert extract_resp.status_code == 200, extract_resp.text

    listed = fresh_app.get("/api/uploads").json()
    assert len(listed) == 1
    assert listed[0]["filename"] == "prospectus.txt"
    assert listed[0]["uploaded_by"] == "promoter"

    download = fresh_app.get(f"/api/uploads/{listed[0]['document_id']}")
    assert download.status_code == 200
    assert download.content == body
    assert download.headers["content-type"].startswith("text/plain")


def test_download_unknown_document_returns_404(fresh_app: TestClient) -> None:
    resp = fresh_app.get("/api/uploads/no-such-document-id")
    assert resp.status_code == 404


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
    fresh_app: TestClient, banker_headers: dict[str, str]
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

    # Advance every blocker through draft → reviewed → certified. Certifying
    # is a banker-only action — the promoter-authenticated default client
    # would get a 403 here.
    for entry_id in blockers:
        for target_state in (SectionState.REVIEWED, SectionState.CERTIFIED):
            resp = fresh_app.post(
                f"/api/review/{entry_id}/advance",
                json={"to": target_state.value},
                headers=banker_headers,
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


def test_illegal_review_advance_returns_409(
    fresh_app: TestClient, banker_headers: dict[str, str]
) -> None:
    """Skipping a state (draft → certified) must surface as HTTP 409."""
    blockers = _blocker_entry_ids()
    assert blockers, "checklist must contain at least one blocker for this test"
    resp = fresh_app.post(
        f"/api/review/{blockers[0]}/advance",
        json={"to": SectionState.CERTIFIED.value},
        headers=banker_headers,
    )
    assert resp.status_code == 409


def test_review_advance_rejects_non_banker_role(fresh_app: TestClient) -> None:
    """The promoter-authenticated default client must be refused (403), not 200."""
    entry_id = _blocker_entry_ids()[0]
    resp = fresh_app.post(
        f"/api/review/{entry_id}/advance", json={"to": SectionState.REVIEWED.value}
    )
    assert resp.status_code == 403


def test_review_edit_records_audit_trail(
    fresh_app: TestClient, banker_headers: dict[str, str]
) -> None:
    entry_id = _blocker_entry_ids()[0]
    edit = {
        "entry_id": entry_id,
        # The server overwrites this with the authenticated banker's email
        # regardless of what's sent here — see the next assertion.
        "editor": "someone-else@example.com",
        "before": "old text",
        "after": "new text",
    }
    resp = fresh_app.post("/api/review/edit", json=edit, headers=banker_headers)
    assert resp.status_code == 200
    recorded = resp.json()["audit_trail"][-1]
    assert recorded["entry_id"] == entry_id
    assert recorded["editor"] == "banker@test.example"
    state_view = fresh_app.get("/api/review/state").json()
    assert any(e["entry_id"] == entry_id for e in state_view["audit_trail"])


# --------------------------------------------------------------------------
# Exchange-ready bundle: same certification lock, then a well-formed ZIP
# --------------------------------------------------------------------------


def test_export_bundle_locked_then_unlocked_zip(
    fresh_app: TestClient, banker_headers: dict[str, str]
) -> None:
    fresh_app.post("/api/generate")

    # Same certification lock as /api/review/export: 409 + blocker list.
    blocked = fresh_app.get("/api/export/bundle")
    assert blocked.status_code == 409, blocked.text
    payload = blocked.json()["detail"]
    assert isinstance(payload, dict) and payload.get("blocked_by")
    assert set(payload["blocked_by"]) == set(_blocker_entry_ids())

    # Certify every blocker (draft → reviewed → certified) — banker-only.
    for entry_id in _blocker_entry_ids():
        for target_state in (SectionState.REVIEWED, SectionState.CERTIFIED):
            resp = fresh_app.post(
                f"/api/review/{entry_id}/advance",
                json={"to": target_state.value},
                headers=banker_headers,
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
    module's real boot-time restore path. ``auth_dir`` is redirected the same
    way session_dir is, so accounts registered here never touch the real
    on-disk user store.
    """
    monkeypatch.setattr(settings, "persist_session", True)
    monkeypatch.setattr(settings, "session_dir", tmp_path / "session")
    monkeypatch.setattr(settings, "auth_dir", tmp_path / "auth")
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "banker_invite_code", TEST_BANKER_INVITE)
    main_module.reset_state()
    reset_user_store()
    try:
        with TestClient(main_module.app) as client:
            promoter_token = _register(
                client, email="promoter2@test.example", name="Test Promoter", role="promoter"
            )
            banker_token = _register(
                client,
                email="banker2@test.example",
                name="Test Banker",
                role="banker",
                invite_code=TEST_BANKER_INVITE,
            )
            client.headers["Authorization"] = f"Bearer {promoter_token}"

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
                f"/api/review/{blocker}/advance",
                json={"to": SectionState.REVIEWED.value},
                headers={"Authorization": f"Bearer {banker_token}"},
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

        # The revived store serves the API exactly like the original — the
        # promoter's account survives the restart too (a real user store, not
        # part of the session snapshot).
        with TestClient(main_module.app) as client:
            client.headers["Authorization"] = f"Bearer {promoter_token}"
            facts = client.get("/api/facts").json()
        assert any(f["fact_id"] == confirmed_id and f["confirmed"] for f in facts)
    finally:
        # Clean slate for later tests; clears the tmp snapshot (session_dir is
        # still monkeypatched here — the real data/session/ is never touched).
        main_module.reset_state()
        reset_user_store()


def test_proposal_accept_role_tagged(
    fresh_app: TestClient, banker_headers: dict[str, str]
) -> None:
    """``supplied_by`` comes from the caller's authenticated role, not a client-set param."""
    proposal = {
        "fact_key": "issue_size_paise",
        "value": 14 * 10**9,
        "source_file": "dd_certificate.pdf",
        "page": 1,
        "snippet": "Issue Size: Rs 14.00 crore",
        "confidence": 0.9,
    }
    fact = fresh_app.post("/api/proposals/accept", json=proposal, headers=banker_headers).json()
    assert fact["supplied_by"] == "banker"


def test_validate_semantic_offline_empty(fresh_app: TestClient) -> None:
    client = fresh_app
    # No key configured in tests -> enrichment silently returns [].
    resp = client.get("/api/validate/semantic")
    assert resp.status_code == 200
    assert resp.json() == []


# --------------------------------------------------------------------------
# Auth: registration, login, and server-side RBAC enforcement
# --------------------------------------------------------------------------


def test_unauthenticated_request_is_rejected(fresh_app: TestClient) -> None:
    """No Authorization header at all -> 401, not a silent empty-store response.

    Uses a brand-new client against the same app rather than ``fresh_app``
    (which carries a promoter token by default) so there is genuinely no
    Authorization header on the wire.
    """
    with TestClient(main_module.app) as anon_client:
        resp = anon_client.get("/api/facts")
    assert resp.status_code == 401


def test_invalid_token_is_rejected(fresh_app: TestClient) -> None:
    resp = fresh_app.get("/api/facts", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_login_round_trips_and_wrong_password_rejected(fresh_app: TestClient) -> None:
    login_ok = fresh_app.post(
        "/api/auth/login",
        json={"email": "promoter@test.example", "password": TEST_PASSWORD},
    )
    assert login_ok.status_code == 200, login_ok.text
    assert login_ok.json()["user"]["role"] == "promoter"

    login_bad = fresh_app.post(
        "/api/auth/login",
        json={"email": "promoter@test.example", "password": "wrong-password"},
    )
    assert login_bad.status_code == 401


def test_banker_registration_requires_valid_invite_code(fresh_app: TestClient) -> None:
    no_code = fresh_app.post(
        "/api/auth/register",
        json={
            "email": "sneaky-banker@test.example",
            "name": "Sneaky",
            "password": TEST_PASSWORD,
            "role": "banker",
        },
    )
    assert no_code.status_code == 403

    wrong_code = fresh_app.post(
        "/api/auth/register",
        json={
            "email": "sneaky-banker2@test.example",
            "name": "Sneaky",
            "password": TEST_PASSWORD,
            "role": "banker",
            "invite_code": "not-the-real-code",
        },
    )
    assert wrong_code.status_code == 403

    right_code = fresh_app.post(
        "/api/auth/register",
        json={
            "email": "real-banker@test.example",
            "name": "Real Banker",
            "password": TEST_PASSWORD,
            "role": "banker",
            "invite_code": TEST_BANKER_INVITE,
        },
    )
    assert right_code.status_code == 200, right_code.text
    assert right_code.json()["user"]["role"] == "banker"


def test_add_fact_supplied_by_is_forced_from_authenticated_role(
    fresh_app: TestClient, banker_headers: dict[str, str]
) -> None:
    """A caller cannot lie about who supplied a fact by setting the field itself."""
    body = {
        "key": "issue_size_paise",
        "value": 14 * 10**9,
        "provenance": {"kind": "wizard", "detail": "test"},
        "supplied_by": "promoter",  # the banker client claims to be the promoter...
    }
    fact = fresh_app.post("/api/facts", json=body, headers=banker_headers).json()
    assert fact["supplied_by"] == "banker"  # ...the server disagrees, correctly


def test_promoter_cannot_certify_and_banker_cannot_generate(
    fresh_app: TestClient, banker_headers: dict[str, str]
) -> None:
    """Cross-check the two role boundaries the certification lock depends on."""
    entry_id = _blocker_entry_ids()[0]
    promoter_tries_to_certify = fresh_app.post(
        f"/api/review/{entry_id}/advance", json={"to": SectionState.REVIEWED.value}
    )
    assert promoter_tries_to_certify.status_code == 403

    banker_tries_to_generate = fresh_app.post("/api/generate", headers=banker_headers)
    assert banker_tries_to_generate.status_code == 403


# --------------------------------------------------------------------------
# Security hardening: body size limits, upload bounds, input validation
# --------------------------------------------------------------------------


def test_oversized_json_body_rejected_by_content_length(
    fresh_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The global body-size middleware rejects by declared Content-Length alone."""
    monkeypatch.setattr(settings, "max_request_body_bytes", 10)
    resp = fresh_app.post(
        "/api/facts",
        json={
            "key": "issuer_identity",
            "value": "Sunrise Agrotech Ltd",
            "provenance": {"kind": "wizard", "detail": "wizard:issuer_identity"},
            "supplied_by": "promoter",
        },
    )
    assert resp.status_code == 413


def test_oversized_upload_rejected(
    fresh_app: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: an oversized upload is refused (413) rather than accepted."""
    monkeypatch.setattr(settings, "max_request_body_bytes", 10)
    body = b"Issue Size: Rs 14.00 crore\n" * 5  # comfortably over the 10-byte cap
    resp = fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("prospectus.txt", body, "text/plain")},
    )
    assert resp.status_code == 413


def test_read_upload_bounded_enforces_limit_independent_of_content_length() -> None:
    """Unit-level check of the bounded-read backstop itself (see main.py docstring):
    it must reject past the byte limit even when nothing upstream has already
    checked Content-Length — e.g. chunked transfer-encoding, or a header that
    understates the true size."""
    import asyncio

    from fastapi import HTTPException, UploadFile

    from app.main import _read_upload_bounded

    async def run() -> None:
        upload = UploadFile(filename="big.txt", file=io.BytesIO(b"a" * 100))
        with pytest.raises(HTTPException) as exc_info:
            await _read_upload_bounded(upload, limit=10)
        assert exc_info.value.status_code == 413

    asyncio.run(run())


def test_upload_filename_is_sanitized_of_path_traversal() -> None:
    from app.main import _sanitize_filename

    assert _sanitize_filename("../../etc/passwd") == "passwd"
    assert _sanitize_filename("..\\..\\secrets.txt") == "secrets.txt"
    assert _sanitize_filename("normal.pdf") == "normal.pdf"
    assert _sanitize_filename("..") == "upload.txt"
    assert _sanitize_filename(None) == "upload.txt"
    assert _sanitize_filename("a\x00b\x1f.txt") == "ab.txt"
    assert len(_sanitize_filename("x" * 500)) == 255  # length-capped, not rejected


def test_eligibility_rejects_out_of_range_input(fresh_app: TestClient) -> None:
    payload = {
        "post_issue_paid_up_capital_paise": -1,  # negative money makes no sense
        "operating_profit_years": 3,
        "min_operating_profit_paise": 2 * 10**9,
        "is_debarred_by_sebi": False,
        "promoter_director_of_debarred_company": False,
        "is_wilful_defaulter_or_fraudulent_borrower": False,
        "is_fugitive_economic_offender": False,
        "has_outstanding_convertibles": False,
        "promoter_change_within_1yr": False,
        "ofs_pct_of_issue": 250.0,  # not a valid percentage
        "promoter_shares_demat": True,
        "partly_paid_shares_outstanding": False,
    }
    resp = fresh_app.post("/api/eligibility", json=payload)
    assert resp.status_code == 422


def test_litigation_query_length_is_bounded(fresh_app: TestClient) -> None:
    resp = fresh_app.get("/api/litigation", params={"entity": "x" * 500})
    assert resp.status_code == 422


def test_register_rejects_overlong_password(fresh_app: TestClient) -> None:
    resp = fresh_app.post(
        "/api/auth/register",
        json={
            "email": "toolong@test.example",
            "name": "Too Long",
            "password": "x" * 5000,
            "role": "promoter",
        },
    )
    assert resp.status_code == 422
