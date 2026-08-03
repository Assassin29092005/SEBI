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

Facts, review state, and user accounts live in Postgres (see ``app.db``);
every test runs inside one DB transaction (the root ``conftest.py``
``db_session`` fixture) that's rolled back at the end, so cases can't leak
facts, review states, or accounts into each other without an explicit reset
call. ``fresh_app`` wires that session into the app via a FastAPI dependency
override and also resets the process-local generated-sections cache (see
``app.runtime_cache``) and any assembled files.

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
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import fitz
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import main as main_module
from app.assemble.bundle import BUNDLE_MEMBERS
from app.audit import reset_audit_log
from app.config import settings
from app.db import get_session
from app.review.workflow import SectionState
from app.schema.models import Severity


DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

TEST_BANKER_INVITE = "test-banker-invite"
TEST_AUDITOR_INVITE = "test-auditor-invite"
TEST_PASSWORD = "Correct-Horse-Battery-Staple-1"  # noqa: S105 — test fixture, not a real secret


async def _register(client: AsyncClient, *, email: str, name: str, role: str, invite_code: str = "") -> str:
    """Register a fresh account and return its bearer token."""
    body: dict[str, Any] = {"email": email, "name": name, "password": TEST_PASSWORD, "role": role}
    if invite_code:
        body["invite_code"] = invite_code
    resp = await client.post("/api/auth/register", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def fresh_app(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    """Wire the per-test DB session into the app and yield a promoter-authenticated AsyncClient.

    ``db_session`` (root ``conftest.py``) is one rolled-back-at-the-end DB
    transaction — every fact/review/account mutation a test makes disappears
    automatically, no explicit reset needed. ``uploads_dir``/``audit_dir``
    are still redirected into ``tmp_path``: the archived-upload vault and
    the audit log are real filesystem directories unaffected by the DB
    rollback, so without the redirect the suite would write into a
    developer's live ``data/uploads/``/``data/audit/``.
    """
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "audit_dir", tmp_path / "audit")
    monkeypatch.setattr(settings, "banker_invite_code", TEST_BANKER_INVITE)
    monkeypatch.setattr(settings, "auditor_invite_code", TEST_AUDITOR_INVITE)
    main_module.reset_runtime_cache()
    reset_audit_log()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    main_module.app.dependency_overrides[get_session] = _override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=main_module.app), base_url="http://testserver"
    ) as client:
        token = await _register(client, email="promoter@test.example", name="Test Promoter", role="promoter")
        client.headers["Authorization"] = f"Bearer {token}"
        yield client
    main_module.app.dependency_overrides.clear()
    main_module.reset_runtime_cache()
    reset_audit_log()


@pytest_asyncio.fixture()
async def banker_headers(fresh_app: AsyncClient) -> dict[str, str]:
    """A second, banker-role account's auth header — for certification-only actions."""
    token = await _register(
        fresh_app,
        email="banker@test.example",
        name="Test Banker",
        role="banker",
        invite_code=TEST_BANKER_INVITE,
    )
    return {"Authorization": f"Bearer {token}"}


def _blocker_entry_ids() -> list[str]:
    return [e.id for e in main_module.checklist.entries if e.severity == Severity.BLOCKER]


async def _seed_fact(
    client: AsyncClient,
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
    resp = await client.post("/api/facts", json=body)
    assert resp.status_code == 200, resp.text
    fact_id = resp.json()["fact_id"]
    if confirmed:
        resp = await client.post(f"/api/facts/{fact_id}/confirm")
        assert resp.status_code == 200, resp.text
    return fact_id


# --------------------------------------------------------------------------
# Simple smoke tests, one per endpoint family
# --------------------------------------------------------------------------


async def test_health_and_schema(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["schema_version"], "checklist must expose a schema_version"

    schema = (await fresh_app.get("/api/schema")).json()
    assert schema["header"]["schema_version"] == body["schema_version"]
    assert isinstance(schema["entries"], list) and schema["entries"]


async def test_eligibility_pass(fresh_app: AsyncClient) -> None:
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
    resp = await fresh_app.post("/api/eligibility", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "pass"
    assert body["items"] == []


async def test_wizard_questions_en_and_hi(fresh_app: AsyncClient) -> None:
    en = await fresh_app.get("/api/wizard/questions?lang=en")
    hi = await fresh_app.get("/api/wizard/questions?lang=hi")
    assert en.status_code == 200 and hi.status_code == 200
    en_questions = en.json()
    hi_questions = hi.json()
    assert en_questions and hi_questions
    # Every question in either language must carry a clause_ref (no orphan questions).
    for q in en_questions + hi_questions:
        assert q["clause_ref"].strip()
    # Same fact-key coverage across languages — copy differs, structure doesn't.
    assert {q["fact_key"] for q in en_questions} == {q["fact_key"] for q in hi_questions}


async def test_facts_crud_add_confirm_correct(fresh_app: AsyncClient) -> None:
    fact_id = await _seed_fact(
        fresh_app,
        key="issuer_identity",
        value="Sunrise Agrotech Ltd",
        detail="wizard:issuer_identity",
        confirmed=False,
    )
    all_facts = (await fresh_app.get("/api/facts")).json()
    assert any(f["fact_id"] == fact_id for f in all_facts)
    assert any(not f["confirmed"] for f in all_facts)

    confirmed = (await fresh_app.post(f"/api/facts/{fact_id}/confirm")).json()
    assert confirmed["confirmed"] is True

    correction = {
        "value": "Sunrise Agrotech Limited",
        "provenance": {"kind": "wizard", "detail": "wizard:issuer_identity (typo fix)"},
    }
    corrected = (await fresh_app.post(f"/api/facts/{fact_id}/correct", json=correction)).json()
    assert corrected["value"] == "Sunrise Agrotech Limited"
    assert corrected["provenance"]["supersedes"] == fact_id


async def test_confirm_and_correct_are_scoped_to_the_supplying_role(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    """A banker may confirm/correct their own banker-sourced fact, but not a
    promoter-sourced one, and vice versa — role-based truth applied to the
    confirmation step itself (see the due-diligence-upload flow)."""
    banker_fact = (
        await fresh_app.post(
            "/api/facts",
            json={
                "key": "due_diligence_certificate",
                "value": "Certified per Reg. 246",
                "provenance": {"kind": "wizard", "detail": "banker upload"},
                "supplied_by": "banker",
            },
            headers=banker_headers,
        )
    ).json()

    # The promoter (fresh_app's default identity) cannot confirm it.
    denied = await fresh_app.post(f"/api/facts/{banker_fact['fact_id']}/confirm")
    assert denied.status_code == 403

    # The banker who supplied it can.
    confirmed = await fresh_app.post(
        f"/api/facts/{banker_fact['fact_id']}/confirm", headers=banker_headers
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["confirmed"] is True

    # Symmetric check: the banker cannot confirm a promoter-sourced fact.
    promoter_fact_id = await _seed_fact(
        fresh_app, key="issuer_identity", value="Sunrise Agrotech Ltd",
        detail="wizard:issuer_identity", confirmed=False,
    )
    denied_other_way = await fresh_app.post(
        f"/api/facts/{promoter_fact_id}/confirm", headers=banker_headers
    )
    assert denied_other_way.status_code == 403

    # And correction follows the same rule.
    correction = {
        "value": "Certified per Reg. 246 (revised)",
        "provenance": {"kind": "wizard", "detail": "banker upload (revised)"},
    }
    promoter_tries_to_correct_banker_fact = await fresh_app.post(
        f"/api/facts/{banker_fact['fact_id']}/correct", json=correction
    )
    assert promoter_tries_to_correct_banker_fact.status_code == 403
    banker_corrects_own_fact = await fresh_app.post(
        f"/api/facts/{banker_fact['fact_id']}/correct", json=correction, headers=banker_headers
    )
    assert banker_corrects_own_fact.status_code == 200, banker_corrects_own_fact.text


async def test_banker_can_correct_a_promoter_supplied_fact_for_due_diligence(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    """The deliberate widening this feature adds: a banker may correct ANY
    fact during due-diligence review, not just their own uploads — the
    promoter/auditor restriction is otherwise unchanged (see the test right
    below this one). This is what makes "banker-correction feedback loop" a
    real signal rather than one scoped to a banker's own rarely-corrected
    uploads."""
    promoter_fact_id = await _seed_fact(
        fresh_app,
        key="issuer_identity",
        value="Sunrise Agrotch Ltd",  # deliberate typo an extraction might produce
        detail="document:prospectus.pdf p.1",
        confirmed=True,
    )

    # Confirmation is UNCHANGED: still 403, a banker can't vouch for a value
    # they didn't supply.
    banker_confirms = await fresh_app.post(
        f"/api/facts/{promoter_fact_id}/confirm", headers=banker_headers
    )
    assert banker_confirms.status_code == 403

    # Correction is the new, deliberately-widened case.
    correction = {
        "value": "Sunrise Agrotech Ltd",
        "provenance": {"kind": "document", "detail": "banker due-diligence review, p.1"},
    }
    resp = await fresh_app.post(
        f"/api/facts/{promoter_fact_id}/correct", json=correction, headers=banker_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["value"] == "Sunrise Agrotech Ltd"
    assert body["provenance"]["supersedes"] == promoter_fact_id
    # supplied_by is preserved from the original fact (still "promoter" —
    # who vouches for the value is unchanged); corrected_by_role records who
    # actually performed THIS correction.
    assert body["supplied_by"] == "promoter"
    assert body["corrected_by_role"] == "banker"


async def test_auditor_still_cannot_correct_a_promoter_supplied_fact(
    fresh_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The widening is BANKER-only — confirms auditor correction rights are
    unchanged, not accidentally broadened alongside banker's."""
    monkeypatch.setattr(settings, "auditor_invite_code", TEST_AUDITOR_INVITE)
    auditor_token = await _register(
        fresh_app,
        email="auditor@test.example",
        name="Test Auditor",
        role="auditor",
        invite_code=TEST_AUDITOR_INVITE,
    )
    promoter_fact_id = await _seed_fact(
        fresh_app, key="issuer_identity", value="Sunrise Agrotech Ltd",
        detail="wizard:issuer_identity", confirmed=True,
    )
    correction = {
        "value": "Sunrise Agrotech Limited",
        "provenance": {"kind": "wizard", "detail": "auditor tries to fix"},
    }
    resp = await fresh_app.post(
        f"/api/facts/{promoter_fact_id}/correct",
        json=correction,
        headers={"Authorization": f"Bearer {auditor_token}"},
    )
    assert resp.status_code == 403


async def test_uploads_extract_txt_payload(fresh_app: AsyncClient) -> None:
    body = b"Issue Size: Rs 14.00 crore\nSme Exchange: NSE Emerge\n"
    resp = await fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("prospectus.txt", body, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    proposals = resp.json()
    fact_keys = {p["fact_key"] for p in proposals}
    assert "issue_size_paise" in fact_keys
    issue = next(p for p in proposals if p["fact_key"] == "issue_size_paise")
    assert issue["value"] == 14 * 10**9  # ₹14 cr → paise


async def test_upload_is_archived_and_downloadable(fresh_app: AsyncClient) -> None:
    """The encrypted document vault: extraction archives the original, and it
    comes back byte-for-byte via the list/download endpoints."""
    body = b"Issue Size: Rs 14.00 crore\nSme Exchange: NSE Emerge\n"
    extract_resp = await fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("prospectus.txt", body, "text/plain")},
    )
    assert extract_resp.status_code == 200, extract_resp.text

    listed = (await fresh_app.get("/api/uploads")).json()
    assert len(listed) == 1
    assert listed[0]["filename"] == "prospectus.txt"
    assert listed[0]["uploaded_by"] == "promoter"

    download = await fresh_app.get(f"/api/uploads/{listed[0]['document_id']}")
    assert download.status_code == 200
    assert download.content == body
    assert download.headers["content-type"].startswith("text/plain")


async def test_download_unknown_document_returns_404(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.get("/api/uploads/no-such-document-id")
    assert resp.status_code == 404


def _make_pdf_bytes(text: str, width: float = 400, height: float = 200) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((20, 40), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


async def test_uploads_extract_proposals_carry_document_id(fresh_app: AsyncClient) -> None:
    """The inline-document-viewer link: every proposal's document_id matches
    the archived upload it was extracted from, not a stray/absent id."""
    body = b"Issue Size: Rs 14.00 crore\n"
    extract_resp = await fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("prospectus.txt", body, "text/plain")},
    )
    proposals = extract_resp.json()
    assert proposals, "expected at least one proposal"

    listed = (await fresh_app.get("/api/uploads")).json()
    assert len(listed) == 1
    archived_id = listed[0]["document_id"]

    assert all(p["document_id"] == archived_id for p in proposals)


async def test_document_page_image_returns_highlighted_png_for_a_real_pdf(
    fresh_app: AsyncClient,
) -> None:
    pdf_bytes = _make_pdf_bytes("Issue Size: Rs 14.00 crore")
    extract_resp = await fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("bank_sanction_letter.pdf", pdf_bytes, "application/pdf")},
    )
    proposals = extract_resp.json()
    issue_size = next(p for p in proposals if p["fact_key"] == "issue_size_paise")
    document_id, page, snippet = issue_size["document_id"], issue_size["page"], issue_size["snippet"]

    plain = await fresh_app.get(f"/api/uploads/{document_id}/page/{page}")
    assert plain.status_code == 200, plain.text
    assert plain.headers["content-type"] == "image/png"

    highlighted = await fresh_app.get(
        f"/api/uploads/{document_id}/page/{page}", params={"snippet": snippet}
    )
    assert highlighted.status_code == 200, highlighted.text
    assert highlighted.headers["content-type"] == "image/png"
    # The highlight annotation actually changed the rendered pixels.
    assert highlighted.content != plain.content


async def test_document_page_image_rejects_non_pdf_upload(fresh_app: AsyncClient) -> None:
    body = b"Issue Size: Rs 14.00 crore\n"
    extract_resp = await fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("prospectus.txt", body, "text/plain")},
    )
    document_id = extract_resp.json()[0]["document_id"]

    resp = await fresh_app.get(f"/api/uploads/{document_id}/page/1")
    assert resp.status_code == 400


async def test_document_page_image_unknown_document_returns_404(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.get("/api/uploads/no-such-document-id/page/1")
    assert resp.status_code == 404


async def test_document_page_image_out_of_range_page_returns_404(fresh_app: AsyncClient) -> None:
    pdf_bytes = _make_pdf_bytes("one page only")
    extract_resp = await fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("single_page.pdf", pdf_bytes, "application/pdf")},
    )
    listed = (await fresh_app.get("/api/uploads")).json()
    document_id = next(d["document_id"] for d in listed if d["filename"] == "single_page.pdf")
    _ = extract_resp

    resp = await fresh_app.get(f"/api/uploads/{document_id}/page/99")
    assert resp.status_code == 404


async def test_document_page_image_zero_page_returns_400(fresh_app: AsyncClient) -> None:
    pdf_bytes = _make_pdf_bytes("one page only")
    await fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("single_page2.pdf", pdf_bytes, "application/pdf")},
    )
    listed = (await fresh_app.get("/api/uploads")).json()
    document_id = next(d["document_id"] for d in listed if d["filename"] == "single_page2.pdf")

    resp = await fresh_app.get(f"/api/uploads/{document_id}/page/0")
    assert resp.status_code == 400


async def test_full_round_trip_confirmed_fact_carries_document_id_page_source_file(
    fresh_app: AsyncClient,
) -> None:
    """Proposal -> accept -> confirm -> GET /api/facts: the inline-viewer
    link survives the whole pipeline, not just the initial proposal."""
    pdf_bytes = _make_pdf_bytes("Issue Size: Rs 14.00 crore")
    extract_resp = await fresh_app.post(
        "/api/uploads/extract",
        files={"file": ("bank_sanction_letter.pdf", pdf_bytes, "application/pdf")},
    )
    proposal = next(
        p for p in extract_resp.json() if p["fact_key"] == "issue_size_paise"
    )

    accepted = await fresh_app.post("/api/proposals/accept", json=proposal)
    fact_id = accepted.json()["fact_id"]
    await fresh_app.post(f"/api/facts/{fact_id}/confirm")

    facts = (await fresh_app.get("/api/facts")).json()
    fact = next(f for f in facts if f["fact_id"] == fact_id)
    assert fact["provenance"]["document_id"] == proposal["document_id"]
    assert fact["provenance"]["page"] == proposal["page"]
    assert fact["provenance"]["source_file"] == "bank_sanction_letter.pdf"


async def test_proposals_accept_creates_unconfirmed_fact(fresh_app: AsyncClient) -> None:
    proposal = {
        "fact_key": "issue_size_paise",
        "value": 14 * 10**9,
        "source_file": "prospectus.txt",
        "page": 1,
        "snippet": "Issue Size: Rs 14.00 crore",
        "confidence": 0.9,
    }
    resp = await fresh_app.post("/api/proposals/accept", json=proposal)
    assert resp.status_code == 200
    fact = resp.json()
    assert fact["key"] == "issue_size_paise"
    assert fact["confirmed"] is False  # accept ≠ confirm
    assert fact["provenance"]["kind"] == "document"


async def test_litigation_returns_records_for_demo_entity(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.get("/api/litigation", params={"entity": "Sunrise Agrotech Ltd"})
    assert resp.status_code == 200
    records = resp.json()
    assert records, "MockLitigationConnector should return demo records for Sunrise Agrotech"
    for rec in records:
        assert rec["case_number"] and rec["forum"]


async def test_generate_caches_sections_and_get_sections_returns_them(fresh_app: AsyncClient) -> None:
    # Empty fact store still yields sections (all-missing gap paragraphs).
    empty = (await fresh_app.get("/api/sections")).json()
    assert empty == []
    generated = (await fresh_app.post("/api/generate")).json()
    assert generated, "generate_all should produce at least one section"
    cached = (await fresh_app.get("/api/sections")).json()
    assert cached == generated


async def test_validate_endpoints_run_over_cached_sections(fresh_app: AsyncClient) -> None:
    await fresh_app.post("/api/generate")
    for path in (
        "/api/validate/contradictions",
        "/api/validate/boilerplate",
        "/api/validate/examiner",
    ):
        resp = await fresh_app.get(path)
        assert resp.status_code == 200, f"{path}: {resp.text}"
        assert isinstance(resp.json(), list)


async def test_iterative_examiner_requires_generate_first(fresh_app: AsyncClient) -> None:
    """Nothing cached to examine — this is a workflow-order 409, not a crash."""
    resp = await fresh_app.post("/api/validate/examiner/iterative")
    assert resp.status_code == 409, resp.text


async def test_iterative_examiner_is_promoter_only(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    """Same restriction as POST /api/generate — it can rewrite draft text."""
    await fresh_app.post("/api/generate")
    resp = await fresh_app.post("/api/validate/examiner/iterative", headers=banker_headers)
    assert resp.status_code == 403


async def test_iterative_examiner_runs_and_caches_final_sections(fresh_app: AsyncClient) -> None:
    await fresh_app.post("/api/generate")
    resp = await fresh_app.post("/api/validate/examiner/iterative")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["rounds"], "at least one round must always run"
    assert report["rounds"][0]["round_number"] == 1
    assert report["stop_reason"] in {
        "survived",
        "no_new_objections",
        "no_revisable_objections",
        "max_rounds_reached",
    }
    assert isinstance(report["survived"], bool)

    # The (possibly revised) final sections are cached the same way
    # POST /api/generate caches its output.
    cached = (await fresh_app.get("/api/sections")).json()
    assert cached == report["final_sections"]


async def test_iterative_examiner_max_rounds_is_bounded(fresh_app: AsyncClient) -> None:
    await fresh_app.post("/api/generate")
    too_low = await fresh_app.post("/api/validate/examiner/iterative", params={"max_rounds": 0})
    assert too_low.status_code == 422
    too_high = await fresh_app.post("/api/validate/examiner/iterative", params={"max_rounds": 6})
    assert too_high.status_code == 422


async def test_validate_arithmetic_returns_findings_list(fresh_app: AsyncClient) -> None:
    """Shape only: an empty store may legitimately yield a missing_inputs finding."""
    resp = await fresh_app.get("/api/validate/arithmetic")
    assert resp.status_code == 200, resp.text
    findings = resp.json()
    assert isinstance(findings, list)
    for finding in findings:
        assert {"kind", "detail", "severity"} <= set(finding)


async def test_suggestions_returns_empty_list_over_an_empty_store(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.get("/api/suggestions")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


async def test_suggestions_surfaces_a_concrete_arithmetic_remediation(
    fresh_app: AsyncClient,
) -> None:
    """Seeds a real GCP-cap breach (15% of a Rs 12.5 cr issue = Rs 1.875 cr;
    GCP set to Rs 2 cr) and confirms /api/suggestions computes the exact
    reconciling amount from the arithmetic finding, not a made-up one."""
    await _seed_fact(fresh_app, key="issue_size_paise", value=12_500_000_000, detail="q:issue_size")
    await _seed_fact(
        fresh_app,
        key="objects_of_issue[]",
        value=[{"purpose": "Working capital", "amount_paise": 8_000_000_000, "deployment_schedule": "FY2027"}],
        detail="q:objects",
    )
    await _seed_fact(fresh_app, key="gcp_amount_paise", value=2_000_000_000, detail="q:gcp")

    arithmetic = (await fresh_app.get("/api/validate/arithmetic")).json()
    breach = next(f for f in arithmetic if f["kind"] == "gcp_cap_breach")
    # 8cr objects + 2cr GCP = 10cr allocated against a 12.5cr issue also
    # leaves a real unallocated-proceeds finding (2.5cr, 20% > the 5%
    # tolerance) — both are genuine, expected findings for these numbers.
    assert len(arithmetic) == 2

    suggestions = (await fresh_app.get("/api/suggestions")).json()
    arithmetic_suggestions = [s for s in suggestions if s["category"] == "arithmetic"]
    assert len(arithmetic_suggestions) == 2
    gcp_suggestion = next(s for s in arithmetic_suggestions if "GCP" in s["message"])
    assert str(breach["expected_paise"]) in gcp_suggestion["message"]


async def test_diff_endpoint_returns_word_level_segments(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.post(
        "/api/diff",
        json={
            "before": "Issue size: Rs 12.50 crore.",
            "after": "Issue size: Rs 14.00 crore.",
        },
    )
    assert resp.status_code == 200, resp.text
    segments = resp.json()
    kinds = [s["kind"] for s in segments]
    assert kinds == ["equal", "delete", "insert", "equal"]
    assert segments[1]["text"] == "12.50"
    assert segments[2]["text"] == "14.00"


async def test_diff_endpoint_identical_text_is_one_equal_segment(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.post(
        "/api/diff", json={"before": "Same text.", "after": "Same text."}
    )
    assert resp.status_code == 200, resp.text
    segments = resp.json()
    assert len(segments) == 1
    assert segments[0]["kind"] == "equal"


async def test_coverage_and_gaps(fresh_app: AsyncClient) -> None:
    await fresh_app.post("/api/generate")
    cov = (await fresh_app.get("/api/coverage")).json()
    assert "sections" in cov and isinstance(cov["sections"], list)
    gaps = (await fresh_app.get("/api/gaps")).json()
    assert "gaps" in gaps and isinstance(gaps["gaps"], list)


# --------------------------------------------------------------------------
# THE DEMO ARC: planted-contradiction detection
# --------------------------------------------------------------------------


async def test_demo_arc_planted_contradiction_is_caught(fresh_app: AsyncClient) -> None:
    """Two confirmed ``issue_size_paise`` facts must show up as a contradiction."""
    # Wizard says ₹12.5 crore (= 12_50_00_00_000 paise), document says ₹14 crore.
    await _seed_fact(
        fresh_app,
        key="issue_size_paise",
        value=125 * 10**8,   # 12.5 crore in paise
        detail="wizard:issue_size",
    )
    await _seed_fact(
        fresh_app,
        key="issue_size_paise",
        value=14 * 10**9,    # 14 crore in paise
        detail="prospectus.txt p.1",
        kind="document",
    )
    await fresh_app.post("/api/generate")
    contradictions = (await fresh_app.get("/api/validate/contradictions")).json()
    assert contradictions, "planted issue_size contradiction was not detected"
    subjects = {c["subject"] for c in contradictions}
    assert any("issue_size" in s for s in subjects), (
        f"expected an issue_size contradiction, got subjects={subjects}"
    )


async def test_enriched_examiner_objects_to_planted_contradiction(fresh_app: AsyncClient) -> None:
    """The examiner now consumes the contradiction check's output as objections."""
    await _seed_fact(
        fresh_app,
        key="issue_size_paise",
        value=125 * 10**8,   # wizard: ₹12.5 crore in paise
        detail="wizard:issue_size",
    )
    await _seed_fact(
        fresh_app,
        key="issue_size_paise",
        value=14 * 10**9,    # document: ₹14 crore in paise
        detail="prospectus.txt p.1",
        kind="document",
    )
    await fresh_app.post("/api/generate")
    resp = await fresh_app.get("/api/validate/examiner")
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


async def test_certification_lock_blocks_then_unlocks_and_files_are_downloadable(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    # Ensure we have generated sections cached for assembly to embed.
    await fresh_app.post("/api/generate")

    # Initial export must be blocked with a non-empty blocker list.
    blocked = await fresh_app.post("/api/review/export")
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
            resp = await fresh_app.post(
                f"/api/review/{entry_id}/advance",
                json={"to": target_state.value},
                headers=banker_headers,
            )
            assert resp.status_code == 200, resp.text

    # Export again — should now succeed and return download URLs.
    unlocked = await fresh_app.post("/api/review/export")
    assert unlocked.status_code == 200, unlocked.text
    urls = unlocked.json()
    assert urls == {"drhp": "/api/assemble/drhp", "abridged": "/api/assemble/abridged"}

    for target in ("drhp", "abridged"):
        resp = await fresh_app.get(f"/api/assemble/{target}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(DOCX_MEDIA_TYPE)
        assert len(resp.content) > 0, f"assembled {target} .docx was empty"


async def test_illegal_review_advance_returns_409(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    """Skipping a state (draft → certified) must surface as HTTP 409."""
    blockers = _blocker_entry_ids()
    assert blockers, "checklist must contain at least one blocker for this test"
    resp = await fresh_app.post(
        f"/api/review/{blockers[0]}/advance",
        json={"to": SectionState.CERTIFIED.value},
        headers=banker_headers,
    )
    assert resp.status_code == 409


async def test_review_advance_rejects_non_banker_role(fresh_app: AsyncClient) -> None:
    """The promoter-authenticated default client must be refused (403), not 200."""
    entry_id = _blocker_entry_ids()[0]
    resp = await fresh_app.post(
        f"/api/review/{entry_id}/advance", json={"to": SectionState.REVIEWED.value}
    )
    assert resp.status_code == 403


async def test_review_edit_records_audit_trail(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
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
    resp = await fresh_app.post("/api/review/edit", json=edit, headers=banker_headers)
    assert resp.status_code == 200
    recorded = resp.json()["audit_trail"][-1]
    assert recorded["entry_id"] == entry_id
    assert recorded["editor"] == "banker@test.example"
    state_view = (await fresh_app.get("/api/review/state")).json()
    assert any(e["entry_id"] == entry_id for e in state_view["audit_trail"])


# --------------------------------------------------------------------------
# Exchange-ready bundle: same certification lock, then a well-formed ZIP
# --------------------------------------------------------------------------


async def test_export_bundle_locked_then_unlocked_zip(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    await fresh_app.post("/api/generate")

    # Same certification lock as /api/review/export: 409 + blocker list.
    blocked = await fresh_app.get("/api/export/bundle")
    assert blocked.status_code == 409, blocked.text
    payload = blocked.json()["detail"]
    assert isinstance(payload, dict) and payload.get("blocked_by")
    assert set(payload["blocked_by"]) == set(_blocker_entry_ids())

    # Certify every blocker (draft → reviewed → certified) — banker-only.
    for entry_id in _blocker_entry_ids():
        for target_state in (SectionState.REVIEWED, SectionState.CERTIFIED):
            resp = await fresh_app.post(
                f"/api/review/{entry_id}/advance",
                json={"to": target_state.value},
                headers=banker_headers,
            )
            assert resp.status_code == 200, resp.text

    resp = await fresh_app.get("/api/export/bundle")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/zip")
    assert "drhp_studio_package.zip" in resp.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        assert set(archive.namelist()) == set(BUNDLE_MEMBERS)
        assert archive.testzip() is None  # every member readable, none corrupt
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        assert manifest["schema_version"] == main_module.checklist.header.schema_version


async def test_proposal_accept_role_tagged(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
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
    fact = (await fresh_app.post("/api/proposals/accept", json=proposal, headers=banker_headers)).json()
    assert fact["supplied_by"] == "banker"


async def test_validate_semantic_offline_empty(fresh_app: AsyncClient) -> None:
    client = fresh_app
    # No key configured in tests -> enrichment silently returns [].
    resp = await client.get("/api/validate/semantic")
    assert resp.status_code == 200
    assert resp.json() == []


# --------------------------------------------------------------------------
# Auth: registration, login, and server-side RBAC enforcement
# --------------------------------------------------------------------------


async def test_unauthenticated_request_is_rejected(fresh_app: AsyncClient) -> None:
    """No Authorization header at all -> 401, not a silent empty-store response.

    Uses a brand-new client against the same app rather than ``fresh_app``
    (which carries a promoter token by default) so there is genuinely no
    Authorization header on the wire.
    """
    async with AsyncClient(
        transport=ASGITransport(app=main_module.app), base_url="http://testserver"
    ) as anon_client:
        resp = await anon_client.get("/api/facts")
    assert resp.status_code == 401


async def test_invalid_token_is_rejected(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.get("/api/facts", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


async def test_login_round_trips_and_wrong_password_rejected(fresh_app: AsyncClient) -> None:
    login_ok = await fresh_app.post(
        "/api/auth/login",
        json={"email": "promoter@test.example", "password": TEST_PASSWORD},
    )
    assert login_ok.status_code == 200, login_ok.text
    assert login_ok.json()["user"]["role"] == "promoter"

    login_bad = await fresh_app.post(
        "/api/auth/login",
        json={"email": "promoter@test.example", "password": "wrong-password"},
    )
    assert login_bad.status_code == 401


async def test_banker_registration_requires_valid_invite_code(fresh_app: AsyncClient) -> None:
    no_code = await fresh_app.post(
        "/api/auth/register",
        json={
            "email": "sneaky-banker@test.example",
            "name": "Sneaky",
            "password": TEST_PASSWORD,
            "role": "banker",
        },
    )
    assert no_code.status_code == 403

    wrong_code = await fresh_app.post(
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

    right_code = await fresh_app.post(
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


async def test_add_fact_supplied_by_is_forced_from_authenticated_role(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    """A caller cannot lie about who supplied a fact by setting the field itself."""
    body = {
        "key": "issue_size_paise",
        "value": 14 * 10**9,
        "provenance": {"kind": "wizard", "detail": "test"},
        "supplied_by": "promoter",  # the banker client claims to be the promoter...
    }
    fact = (await fresh_app.post("/api/facts", json=body, headers=banker_headers)).json()
    assert fact["supplied_by"] == "banker"  # ...the server disagrees, correctly


async def test_promoter_cannot_certify_and_banker_cannot_generate(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    """Cross-check the two role boundaries the certification lock depends on."""
    entry_id = _blocker_entry_ids()[0]
    promoter_tries_to_certify = await fresh_app.post(
        f"/api/review/{entry_id}/advance", json={"to": SectionState.REVIEWED.value}
    )
    assert promoter_tries_to_certify.status_code == 403

    banker_tries_to_generate = await fresh_app.post("/api/generate", headers=banker_headers)
    assert banker_tries_to_generate.status_code == 403


# --------------------------------------------------------------------------
# Security hardening: body size limits, upload bounds, input validation
# --------------------------------------------------------------------------


async def test_oversized_json_body_rejected_by_content_length(
    fresh_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The global body-size middleware rejects by declared Content-Length alone."""
    monkeypatch.setattr(settings, "max_request_body_bytes", 10)
    resp = await fresh_app.post(
        "/api/facts",
        json={
            "key": "issuer_identity",
            "value": "Sunrise Agrotech Ltd",
            "provenance": {"kind": "wizard", "detail": "wizard:issuer_identity"},
            "supplied_by": "promoter",
        },
    )
    assert resp.status_code == 413


async def test_oversized_upload_rejected(
    fresh_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: an oversized upload is refused (413) rather than accepted."""
    monkeypatch.setattr(settings, "max_request_body_bytes", 10)
    body = b"Issue Size: Rs 14.00 crore\n" * 5  # comfortably over the 10-byte cap
    resp = await fresh_app.post(
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


async def test_eligibility_rejects_out_of_range_input(fresh_app: AsyncClient) -> None:
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
    resp = await fresh_app.post("/api/eligibility", json=payload)
    assert resp.status_code == 422


async def test_litigation_query_length_is_bounded(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.get("/api/litigation", params={"entity": "x" * 500})
    assert resp.status_code == 422


async def test_register_rejects_overlong_password(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.post(
        "/api/auth/register",
        json={
            "email": "toolong@test.example",
            "name": "Too Long",
            "password": "x" * 5000,
            "role": "promoter",
        },
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------
# Audit log: who accessed or changed what, and when
# --------------------------------------------------------------------------


async def test_audit_log_is_banker_only(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.get("/api/audit")
    assert resp.status_code == 403


async def test_audit_log_records_promoter_actions_with_correct_actor(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    fact_id = await _seed_fact(
        fresh_app, key="issuer_identity", value="Sunrise Agrotech Ltd", detail="wizard:issuer_identity"
    )

    events = (await fresh_app.get("/api/audit", headers=banker_headers)).json()
    actions = {(e["action"], e["actor_email"]) for e in events}
    assert ("add_fact", "promoter@test.example") in actions
    assert ("confirm_fact", "promoter@test.example") in actions

    confirm_events = [e for e in events if e["action"] == "confirm_fact"]
    assert any(e["resource_id"] == fact_id for e in confirm_events)
    assert all(e["outcome"] == "success" for e in confirm_events)


async def test_audit_log_records_access_denials(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    entry_id = _blocker_entry_ids()[0]
    denied = await fresh_app.post(f"/api/review/{entry_id}/advance", json={"to": "reviewed"})
    assert denied.status_code == 403

    events = (
        await fresh_app.get("/api/audit", headers=banker_headers, params={"outcome": "denied"})
    ).json()
    assert any(
        e["action"] == "advance_review" and e["actor_email"] == "promoter@test.example"
        for e in events
    )


async def test_audit_log_records_login_success_and_failure(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    ok = await fresh_app.post(
        "/api/auth/login", json={"email": "promoter@test.example", "password": TEST_PASSWORD}
    )
    assert ok.status_code == 200

    bad = await fresh_app.post(
        "/api/auth/login",
        json={"email": "promoter@test.example", "password": "definitely-wrong"},
    )
    assert bad.status_code == 401

    events = (await fresh_app.get("/api/audit", headers=banker_headers)).json()
    logins = [e for e in events if e["action"] == "login"]
    assert any(
        e["outcome"] == "success" and e["actor_email"] == "promoter@test.example" for e in logins
    )
    assert any(
        e["outcome"] == "denied" and e["actor_email"] == "promoter@test.example" for e in logins
    )


async def test_audit_log_records_registration(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    events = (await fresh_app.get("/api/audit", headers=banker_headers)).json()
    registered_emails = {e["actor_email"] for e in events if e["action"] == "register"}
    # fresh_app registered the promoter, banker_headers registered the banker.
    assert "promoter@test.example" in registered_emails
    assert "banker@test.example" in registered_emails


async def test_audit_log_records_document_download(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    body = b"Issue Size: Rs 14.00 crore\n"
    await fresh_app.post(
        "/api/uploads/extract", files={"file": ("prospectus.txt", body, "text/plain")}
    )
    doc_id = (await fresh_app.get("/api/uploads")).json()[0]["document_id"]
    await fresh_app.get(f"/api/uploads/{doc_id}")

    events = (await fresh_app.get("/api/audit", headers=banker_headers)).json()
    downloads = [e for e in events if e["action"] == "download_document"]
    assert any(e["resource_id"] == doc_id and e["outcome"] == "success" for e in downloads)


async def test_audit_log_filters_by_actor_email_via_query_param(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    await fresh_app.get("/api/facts")  # a promoter-attributed action to filter for
    only_promoter = (
        await fresh_app.get(
            "/api/audit", headers=banker_headers, params={"actor_email": "promoter@test.example"}
        )
    ).json()
    assert only_promoter
    assert all(e["actor_email"] == "promoter@test.example" for e in only_promoter)


async def test_audit_log_excludes_health_check(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    await fresh_app.get("/api/health")
    await fresh_app.get("/api/health")
    events = (await fresh_app.get("/api/audit", headers=banker_headers)).json()
    assert not any(e["path"] == "/api/health" for e in events)


# --------------------------------------------------------------------------
# Regulatory staleness watcher (see app.regulatory_watch) — the real
# connector is swapped for a fake one via monkeypatch so the standard suite
# never hits the live network; test_regulatory_watch.py has the one real,
# self-skipping live-network test.
# --------------------------------------------------------------------------


class _FakeRegulatoryWatchConnector:
    def __init__(self, updates: list[Any]) -> None:
        self._updates = updates

    async def check_for_updates(self, since: Any) -> list[Any]:
        return [u for u in self._updates if u.published > since]


async def test_regulatory_watch_check_is_banker_only(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.post("/api/regulatory-watch/check")
    assert resp.status_code == 403


async def test_regulatory_watch_status_is_banker_only(fresh_app: AsyncClient) -> None:
    resp = await fresh_app.get("/api/regulatory-watch/status")
    assert resp.status_code == 403


async def test_regulatory_watch_status_before_any_check_is_null(
    fresh_app: AsyncClient, banker_headers: dict[str, str]
) -> None:
    resp = await fresh_app.get("/api/regulatory-watch/status", headers=banker_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() is None


async def test_regulatory_watch_check_clean_when_nothing_newer(
    fresh_app: AsyncClient, banker_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_module.runtime_cache, "regulatory_watch_connector", _FakeRegulatoryWatchConnector([]))
    resp = await fresh_app.post("/api/regulatory-watch/check", headers=banker_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["checked_successfully"] is True
    assert body["newer_updates"] == []
    assert body["pinned_amended_through"] == main_module.checklist.header.amended_through


async def test_regulatory_watch_check_and_status_round_trip(
    fresh_app: AsyncClient, banker_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.regulatory_watch import RegulatoryUpdate

    future_update = RegulatoryUpdate(
        title="Consultation Paper on certain Amendments to SEBI (ICDR) Regulations, 2018",
        published=date(2099, 1, 1),
        url="https://www.sebi.gov.in/reports/example.html",
    )
    monkeypatch.setattr(
        main_module.runtime_cache,
        "regulatory_watch_connector",
        _FakeRegulatoryWatchConnector([future_update]),
    )
    checked = await fresh_app.post("/api/regulatory-watch/check", headers=banker_headers)
    assert checked.status_code == 200, checked.text
    checked_body = checked.json()
    assert checked_body["checked_successfully"] is True
    assert len(checked_body["newer_updates"]) == 1
    assert checked_body["newer_updates"][0]["title"] == future_update.title

    # The check's result is cached — a subsequent status read (no new
    # network call) returns exactly what was just found.
    status = await fresh_app.get("/api/regulatory-watch/status", headers=banker_headers)
    assert status.status_code == 200, status.text
    assert status.json() == checked_body


async def test_regulatory_watch_check_honestly_degrades_on_connector_failure(
    fresh_app: AsyncClient, banker_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.regulatory_watch import RegulatoryWatchUnavailable

    class _FailingConnector:
        async def check_for_updates(self, since: Any) -> list[Any]:
            raise RegulatoryWatchUnavailable("simulated network failure")

    monkeypatch.setattr(main_module.runtime_cache, "regulatory_watch_connector", _FailingConnector())
    resp = await fresh_app.post("/api/regulatory-watch/check", headers=banker_headers)
    assert resp.status_code == 200, resp.text  # a failed live check is not itself an API error
    body = resp.json()
    assert body["checked_successfully"] is False
    assert body["newer_updates"] == []
    assert body["source"] == "unavailable"
