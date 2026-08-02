"""FastAPI app tying the pipeline together.

Facts, review state, and user accounts are durable in Postgres (see
``app.db``, ``app.db_models``, ``app.facts_repo``, ``app.review.repo``,
``app.auth.store``) — one deployment still serves one issuer's
promoter/auditor/banker team (single-tenant: see ``app.auth`` for why that's
the right unit of isolation here), but a backend restart or crash no longer
loses the session; durability comes from Postgres's own write-ahead log, not
from an application-level snapshot file. The only process-local state left is
the generated-sections cache (see ``app.runtime_cache``) — cheap and
deterministic to regenerate from confirmed facts, so it was never worth a
table.

Every endpoint below requires a valid JWT bearer token (``app.auth.dependencies
.get_current_user``); actions scoped to one role (confirming/correcting a
fact is scoped to whoever supplied it, certifying a section is banker-only)
additionally depend on ``require_roles(...)`` — the frontend's old role
dropdown was UI-only, this is server-enforced. Uploaded source documents are
archived encrypted (see :mod:`app.intake.vault`) so a banker/auditor can
retrieve the original a fact's snippet came from.

Each request that touches durable state takes its own DB session via
``Depends(get_session)`` (see ``app.db``); there is no module-level mutable
state to reset between tests any more — ``reset_runtime_cache()`` only clears
the generated-sections cache and any assembled files, since the DB-backed
data is reset by the test suite's own transaction-rollback fixture.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import facts_repo, runtime_cache
from app.assemble.bundle import build_bundle
from app.assemble.docx_builder import assemble
from app.audit import (
    AuditEvent,
    Outcome,
    classify_request,
    get_audit_log,
    outcome_for_status,
)
from app.auth import store as auth_store
from app.auth.dependencies import get_current_user, require_roles
from app.auth.models import User
from app.auth.router import router as auth_router
from app.auth.security import InvalidToken, decode_access_token
from app.config import settings
from app.coverage import BenchmarkReport, CoverageReport, benchmark, score
from app.db import get_session
from app.eligibility import EligibilityInput, EligibilityReport, evaluate
from app.extraction_reliability import ExtractionReliabilityReport, compute_reliability
from app.facts import Fact, FactStore, Provenance
from app.facts_repo import FactNotFound
from app.generate.sections import GeneratedSection, generate_all
from app.intake.litigation import LitigationRecord
from app.intake.uploads import ExtractionProposal, extract_facts, proposal_to_fact
from app.intake.vault import (
    ArchivedDocumentMeta,
    archive_upload,
    list_archived_uploads,
    retrieve_upload,
)
from app.intake.wizard import WizardQuestion, derive_questions
from app.review import repo as review_repo
from app.review.workflow import BankerEdit, ReviewState, SectionState, export_allowed
from app.schema.loader import load_checklist
from app.schema.models import Checklist, OutputTarget, Role
from app.validate.arithmetic import ArithmeticFinding, check_arithmetic
from app.validate.boilerplate import BoilerplateFlag, detect
from app.validate.contradictions import (
    Claim,
    Contradiction,
    cross_check,
    extract_claims,
    semantic_check,
)
from app.validate.examiner import Objection, examine
from app.validate.gaps import GapReport, check_gaps
from app.validate.iterative_examiner import IterativeExaminationReport, examine_iteratively

logger = logging.getLogger("drhp.main")

app = FastAPI(title="DRHP Studio", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def limit_body_size(request: Request, call_next: Any) -> Any:
    """Reject an oversized request before it's read into memory.

    Covers every endpoint (JSON bodies, multipart uploads) via the declared
    ``Content-Length`` header — a fast, cheap rejection that avoids buffering
    a huge body just to throw it away. This is a fast-path only: a request
    that lies about (or omits, e.g. chunked transfer-encoding) its
    Content-Length is not caught here. The upload endpoint additionally
    enforces the same limit while actually reading the file (see
    ``_read_upload_bounded``), which is the real backstop for that one
    high-risk surface; every other endpoint's body is tiny JSON with no
    unbounded-read path in the first place.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            return JSONResponse({"detail": "invalid Content-Length header"}, status_code=400)
        if declared > settings.max_request_body_bytes:
            return JSONResponse(
                {
                    "detail": (
                        f"request body of {declared} bytes exceeds the "
                        f"{settings.max_request_body_bytes} byte limit"
                    )
                },
                status_code=413,
            )
    return await call_next(request)


_AUDIT_EXCLUDED_PATHS = {"/api/health"}  # pure liveness-check noise, never security-relevant
_AUTH_RESPONSE_BODY_PATHS = {"/api/auth/register", "/api/auth/login"}


async def _resolve_actor(request: Request) -> tuple[str | None, str, Role | None]:
    """Best-effort actor identification from the Authorization header.

    Returns ``(user_id, email, role)``; email defaults to ``"anonymous"`` and
    the other two to ``None`` when there's no valid, resolvable token — which
    is expected for public endpoints and for requests that never
    authenticated at all (not an error condition to raise on).
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None, "anonymous", None
    token = header[len("bearer ") :].strip()
    try:
        payload = decode_access_token(token)
    except InvalidToken:
        return None, "anonymous", None
    # Middleware has no FastAPI Depends() injection, so this drives the
    # get_session generator by hand rather than calling get_sessionmaker()
    # directly — going through request.app.dependency_overrides means the
    # test suite's per-test-transaction override applies here too, not just
    # to route handlers. Calling get_sessionmaker() directly used to bypass
    # that override on every single request, which meant every HTTP call in
    # the test suite opened a stray connection on app.db's global,
    # never-reset production engine — pytest-asyncio hands out a fresh event
    # loop per test, so that engine's pooled connections went stale across
    # tests and asyncpg raised "Event loop is closed" trying to close them.
    session_dependency = request.app.dependency_overrides.get(get_session, get_session)
    session_gen = session_dependency()
    session = await anext(session_gen)
    try:
        user = await auth_store.get_by_id(session, payload.get("sub", ""))
    finally:
        await session_gen.aclose()
    if user is None or user.disabled:
        return None, "anonymous", None
    return user.user_id, user.email, user.role


@app.middleware("http")
async def audit_log_middleware(request: Request, call_next: Any) -> Any:
    """Record who accessed or changed what, and when — see :mod:`app.audit`.

    Registered after ``limit_body_size`` so it wraps that layer too (a 413
    rejection is itself worth an audit record). Every step here is
    defensive: a bug in audit logging must never take down the actual
    request it's observing.
    """
    path = request.url.path
    if path in _AUDIT_EXCLUDED_PATHS:
        return await call_next(request)

    user_id, actor_email, actor_role = await _resolve_actor(request)

    # Login has no token yet — peek the submitted email so a *failed* login
    # attempt is still attributable. Reading the body here is safe: Starlette
    # caches it, so the route handler's own body parsing downstream still
    # works off the same cached bytes.
    attempted_login_email: str | None = None
    if path == "/api/auth/login" and request.method == "POST":
        try:
            attempted_login_email = json.loads(await request.body()).get("email")
        except Exception:  # noqa: BLE001 — best-effort only, never blocks the request
            attempted_login_email = None

    response = await call_next(request)

    # Login/register succeed with no prior token — the actor IS the account
    # that request just authenticated/created. Buffer + replay the response
    # body so we can read it without breaking the client's copy.
    if path in _AUTH_RESPONSE_BODY_PATHS and response.status_code == 200:
        body = b"".join([chunk async for chunk in response.body_iterator])
        response = Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
        try:
            user_payload = json.loads(body)["user"]
            actor_email = user_payload["email"]
            actor_role = user_payload["role"]
        except Exception:  # noqa: BLE001 — malformed body is not this middleware's problem
            pass
    elif path == "/api/auth/login" and attempted_login_email:
        actor_email = attempted_login_email

    try:
        action, resource_type, resource_id = classify_request(request.method, path)
        get_audit_log().record(
            AuditEvent(
                actor_user_id=user_id,
                actor_email=actor_email,
                actor_role=actor_role,
                method=request.method,
                path=path,
                status_code=response.status_code,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome_for_status(response.status_code),
            )
        )
    except Exception:  # noqa: BLE001 — audit logging must never break the real request
        logger.exception("audit logging failed for %s %s", request.method, path)

    return response


app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

# Assembled .docx files land here; the directory is created on demand and is
# gitignored (see .gitignore).
OUT_DIR: Path = Path(__file__).resolve().parents[2] / "out"

# The checklist is the versioned schema — module-level, load once.
checklist: Checklist = load_checklist()


def reset_runtime_cache() -> None:
    """Clear the generated-sections cache and any assembled files.

    Test-only. Facts, review state, and user accounts reset via the test
    suite's own DB-transaction rollback (see root ``conftest.py``'s
    ``db_session`` fixture) — there is no module-level store to swap out here
    any more.
    """
    runtime_cache.reset_cache()
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR, ignore_errors=True)
    if settings.uploads_dir.exists():
        shutil.rmtree(settings.uploads_dir, ignore_errors=True)


# --------------------------------------------------------------------------
# Request/response shapes local to the API layer
# --------------------------------------------------------------------------


class CorrectionRequest(BaseModel):
    value: Any
    provenance: Provenance


class AdvanceRequest(BaseModel):
    to: SectionState


class ExportResponse(BaseModel):
    drhp: str
    abridged: str


# --------------------------------------------------------------------------
# Health / schema
# --------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "schema_version": checklist.header.schema_version}


@app.get("/api/schema")
async def get_schema() -> Checklist:
    return checklist


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------


@app.post("/api/eligibility")
async def eligibility(
    data: EligibilityInput, _user: User = Depends(require_roles(Role.PROMOTER))
) -> EligibilityReport:
    return evaluate(data)


# --------------------------------------------------------------------------
# Wizard
# --------------------------------------------------------------------------


@app.get("/api/wizard/questions")
async def wizard_questions(
    lang: str = Query(default="en"),
    _user: User = Depends(require_roles(Role.PROMOTER)),
) -> list[WizardQuestion]:
    return derive_questions(checklist, lang=lang)


# --------------------------------------------------------------------------
# Facts
# --------------------------------------------------------------------------


@app.get("/api/facts")
async def list_facts(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Fact]:
    """Return every fact in the store — confirmed AND unconfirmed.

    The UI needs unconfirmed proposals visible so the promoter can act on them;
    generation still ignores anything not confirmed (see FactStore.confirmed_by_key).
    """
    return await facts_repo.all_facts(session)


@app.post("/api/facts")
async def add_fact(
    fact: Fact,
    current_user: User = Depends(require_roles(Role.PROMOTER, Role.AUDITOR, Role.BANKER)),
    session: AsyncSession = Depends(get_session),
) -> Fact:
    """Add a fact. ``supplied_by`` is always the caller's authenticated role —
    role-based truth (who may lawfully supply which content) would be
    meaningless if a client could just set this field itself."""
    return await facts_repo.add(session, fact.model_copy(update={"supplied_by": current_user.role}))


async def _get_fact_or_404(session: AsyncSession, fact_id: str) -> Fact:
    try:
        return await facts_repo.get(session, fact_id)
    except FactNotFound as exc:
        raise HTTPException(status_code=404, detail=f"fact not found: {fact_id}") from exc


async def _require_own_fact(session: AsyncSession, fact_id: str, current_user: User) -> Fact:
    """Look up ``fact_id``, 404 if missing, 403 unless the caller supplied it.

    Used by CONFIRMATION only (see ``confirm_fact``) — confirming a fact is
    the supplying party vouching for it, so it must be that same party, not
    a different role rubber-stamping someone else's submission. Correction
    has a broader rule; see ``_require_can_correct_fact``.
    """
    fact = await _get_fact_or_404(session, fact_id)
    if fact.supplied_by != current_user.role:
        raise HTTPException(
            status_code=403,
            detail=(
                f"only the {fact.supplied_by.value} who supplied this fact may confirm it "
                f"(you are {current_user.role.value})"
            ),
        )
    return fact


async def _require_can_correct_fact(
    session: AsyncSession, fact_id: str, current_user: User
) -> Fact:
    """Look up ``fact_id``, 404 if missing, 403 unless the caller may correct it.

    The supplying party may always correct their own fact (same rule as
    confirmation). A BANKER may additionally correct ANY fact regardless of
    who supplied it — this is the due-diligence review authority the
    problem statement requires the merchant banker to hold: catching a
    promoter's misextracted figure during due diligence is exactly what
    banker review is for, and a banker restricted to only their own
    due-diligence-certificate uploads couldn't do that job at all. This is
    strictly an ADDITIVE widening for BANKER only — promoter and auditor
    correction rights are unchanged, confirmation is untouched for every
    role, and every correction (whoever performs it) is recorded with
    exactly who did it (``Fact.corrected_by_role``, see app.facts) and
    captured in the audit log like any other request — a banker correcting
    someone else's fact is MORE visible after this change, not less.
    """
    fact = await _get_fact_or_404(session, fact_id)
    if fact.supplied_by == current_user.role or current_user.role == Role.BANKER:
        return fact
    raise HTTPException(
        status_code=403,
        detail=(
            f"only the {fact.supplied_by.value} who supplied this fact, or a banker doing "
            f"due-diligence review, may correct it (you are {current_user.role.value})"
        ),
    )


@app.post("/api/facts/{fact_id}/confirm")
async def confirm_fact(
    fact_id: str,
    current_user: User = Depends(require_roles(Role.PROMOTER, Role.AUDITOR, Role.BANKER)),
    session: AsyncSession = Depends(get_session),
) -> Fact:
    await _require_own_fact(session, fact_id, current_user)
    return await facts_repo.confirm(session, fact_id)


@app.post("/api/facts/{fact_id}/correct")
async def correct_fact(
    fact_id: str,
    req: CorrectionRequest,
    current_user: User = Depends(require_roles(Role.PROMOTER, Role.AUDITOR, Role.BANKER)),
    session: AsyncSession = Depends(get_session),
) -> Fact:
    await _require_can_correct_fact(session, fact_id, current_user)
    try:
        return await facts_repo.correct(
            session, fact_id, req.value, req.provenance, corrected_by_role=current_user.role
        )
    except FactNotFound as exc:
        raise HTTPException(status_code=404, detail=f"fact not found: {fact_id}") from exc


# --------------------------------------------------------------------------
# Uploads / extraction / proposals
# --------------------------------------------------------------------------

_UPLOAD_CHUNK_BYTES = 1024 * 1024  # 1 MiB per read
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f]")


async def _read_upload_bounded(file: UploadFile, limit: int) -> bytes:
    """Read ``file`` in chunks, aborting with 413 past ``limit`` bytes.

    The body-size middleware already rejects most oversized requests by
    ``Content-Length`` before any bytes are read; this is the real backstop
    for this endpoint specifically — it holds even if that header is absent,
    wrong, or the multipart framing hides the true size until the file part
    is actually streamed.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=413, detail=f"upload exceeds the {limit} byte limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _sanitize_filename(raw: str | None) -> str:
    """Basename-only, control-character-free, length-capped.

    Nothing today uses the client-supplied filename to build a filesystem
    path (extraction runs entirely in memory over the bytes) — but the name
    does flow into fact provenance, generated document text, and the
    exported audit-trail JSON, so it must not carry path syntax or control
    characters, and can't be allowed to be unbounded. Treated as untrusted
    display text, not a path, but sanitised as if it might become one.
    """
    if not raw:
        return "upload.txt"
    name = os.path.basename(raw.replace("\\", "/"))
    name = _CONTROL_CHARS_RE.sub("", name).strip()
    if not name or name in {".", ".."}:
        return "upload.txt"
    return name[:255]


@app.post("/api/uploads/extract")
async def uploads_extract(
    file: Annotated[UploadFile, File(...)],
    current_user: User = Depends(require_roles(Role.PROMOTER, Role.AUDITOR, Role.BANKER)),
) -> list[ExtractionProposal]:
    content = await _read_upload_bounded(file, settings.max_request_body_bytes)
    filename = _sanitize_filename(file.filename)
    # Archive the original, encrypted, before extraction — so a later banker/
    # auditor review can check a fact's snippet against the real source
    # document, not just trust the snippet text. Archiving is best-effort:
    # a write failure here must not block the extraction the promoter is
    # waiting on, so it's logged rather than raised.
    try:
        archive_upload(content, filename, file.content_type or "", current_user.role)
    except OSError:
        logger.warning("failed to archive upload %r — extraction proceeds anyway", filename)
    return await extract_facts(filename, content)


@app.get("/api/uploads")
async def list_uploads(_user: User = Depends(get_current_user)) -> list[ArchivedDocumentMeta]:
    """Metadata for every archived source document (not the bytes — see the download route)."""
    return list_archived_uploads()


@app.get("/api/uploads/{document_id}")
async def download_upload(
    document_id: str, _user: User = Depends(get_current_user)
) -> Response:
    """Decrypt and stream back one archived original document."""
    result = retrieve_upload(document_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"document not found: {document_id}")
    meta, content = result
    return Response(
        content=content,
        media_type=meta.content_type,
        headers={"Content-Disposition": f'attachment; filename="{meta.filename}"'},
    )


@app.post("/api/proposals/accept")
async def proposals_accept(
    proposal: ExtractionProposal,
    current_user: User = Depends(require_roles(Role.PROMOTER, Role.AUDITOR, Role.BANKER)),
    session: AsyncSession = Depends(get_session),
) -> Fact:
    """Materialise a proposal into an unconfirmed Fact in the store.

    Confirmation is a separate step (POST /api/facts/{id}/confirm) — the
    unconfirmed fact never feeds generation. The supplying role (role-based
    truth: auditor/banker uploads enter as that role) is the caller's
    authenticated role — previously a client-supplied query param, which let
    anyone tag their upload as coming from any role they liked.
    """
    return await facts_repo.add(session, proposal_to_fact(proposal, supplied_by=current_user.role))


# --------------------------------------------------------------------------
# Litigation lookup
# --------------------------------------------------------------------------


@app.get("/api/litigation")
async def litigation(
    entity: str = Query(..., min_length=1, max_length=200),
    _user: User = Depends(get_current_user),
) -> list[LitigationRecord]:
    return await runtime_cache.litigation_connector.search(entity, {})


# --------------------------------------------------------------------------
# Generation + cached sections
# --------------------------------------------------------------------------


@app.post("/api/generate")
async def generate(
    _user: User = Depends(require_roles(Role.PROMOTER)),
    session: AsyncSession = Depends(get_session),
) -> list[GeneratedSection]:
    """Run grounded generation over the current fact store and cache the result.

    Cached via ``app.runtime_cache``; readable via ``GET /api/sections``.
    """
    store = await facts_repo.load_fact_store(session)
    sections = await generate_all(checklist, store)
    runtime_cache.set_generated_sections(sections)
    return sections


@app.get("/api/sections")
async def sections(_user: User = Depends(get_current_user)) -> list[GeneratedSection]:
    """Return the last generated sections (empty list if never generated)."""
    return runtime_cache.get_generated_sections()


# --------------------------------------------------------------------------
# Validation (runs over cached sections)
# --------------------------------------------------------------------------


async def _current_contradictions(store: FactStore) -> list[Contradiction]:
    """Cross-check numeric/entity claims across the cached sections + store."""
    all_claims: list[Claim] = []
    for section in runtime_cache.get_generated_sections():
        all_claims.extend(await extract_claims(section, store))
    return cross_check(all_claims)


def _current_boilerplate() -> list[BoilerplateFlag]:
    flags: list[BoilerplateFlag] = []
    for section in runtime_cache.get_generated_sections():
        flags.extend(detect(section))
    return flags


async def _examiner_objections(
    store: FactStore,
    contradictions: list[Contradiction],
    arithmetic: list[ArithmeticFinding],
) -> list[Objection]:
    """Enriched examiner: the other validators' outputs feed its deterministic pass."""
    return await examine(
        runtime_cache.get_generated_sections(),
        checklist=checklist,
        contradictions=contradictions,
        boilerplate_flags=_current_boilerplate(),
        arithmetic_findings=arithmetic,
        store=store,
    )


@app.get("/api/validate/contradictions")
async def validate_contradictions(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Contradiction]:
    store = await facts_repo.load_fact_store(session)
    return await _current_contradictions(store)


@app.get("/api/validate/semantic")
async def validate_semantic(_user: User = Depends(get_current_user)) -> list[Contradiction]:
    """Free-prose cross-section consistency (LLM enrichment; [] offline)."""
    return await semantic_check(runtime_cache.get_generated_sections())


@app.get("/api/validate/boilerplate")
async def validate_boilerplate(_user: User = Depends(get_current_user)) -> list[BoilerplateFlag]:
    return _current_boilerplate()


@app.get("/api/validate/arithmetic")
async def validate_arithmetic(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ArithmeticFinding]:
    """Objects-of-the-Issue arithmetic over confirmed facts (deterministic, no LLM)."""
    store = await facts_repo.load_fact_store(session)
    return check_arithmetic(store)


@app.get("/api/validate/examiner")
async def validate_examiner(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Objection]:
    store = await facts_repo.load_fact_store(session)
    contradictions = await _current_contradictions(store)
    arithmetic = check_arithmetic(store)
    return await _examiner_objections(store, contradictions, arithmetic)


@app.post("/api/validate/examiner/iterative")
async def validate_examiner_iterative(
    max_rounds: int = Query(3, ge=1, le=5),
    _user: User = Depends(require_roles(Role.PROMOTER)),
    session: AsyncSession = Depends(get_session),
) -> IterativeExaminationReport:
    """Loop the adversarial examiner — revising sections and re-examining —
    until a round raises no objection unseen in an earlier round.

    Promoter-only, same as POST /api/generate: this can rewrite section text
    via the LLM, which is generation-adjacent, not a read. Requires
    /api/generate to have run first (there is nothing cached to examine
    otherwise). On return, the cached sections are updated to
    ``report.final_sections`` — same caching contract as /api/generate — so
    GET /api/sections, /api/coverage, /api/gaps, and the export bundle all
    see the post-revision draft, not the pre-revision one.

    ``max_rounds`` is bounded [1, 5]: each round can trigger one LLM call per
    section still under objection, so an unbounded client-supplied value
    would be an uncapped-cost lever (see CLAUDE.md's sanity-bound convention
    for other numeric inputs).
    """
    store = await facts_repo.load_fact_store(session)
    sections = runtime_cache.get_generated_sections()
    if not sections:
        raise HTTPException(
            status_code=409, detail="no generated sections — run POST /api/generate first"
        )
    arithmetic = check_arithmetic(store)
    report = await examine_iteratively(
        sections, checklist, store, arithmetic_findings=arithmetic, max_rounds=max_rounds
    )
    runtime_cache.set_generated_sections(report.final_sections)
    return report


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


@app.get("/api/coverage")
async def coverage(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CoverageReport:
    store = await facts_repo.load_fact_store(session)
    return score(checklist, runtime_cache.get_generated_sections(), store=store)


@app.get("/api/coverage/benchmark")
async def coverage_benchmark(_user: User = Depends(get_current_user)) -> BenchmarkReport:
    """Schema coverage of real filed SME DRHP tables of contents (evidence, not a claim)."""
    return benchmark(checklist)


# --------------------------------------------------------------------------
# Gaps
# --------------------------------------------------------------------------


@app.get("/api/gaps")
async def gaps(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> GapReport:
    store = await facts_repo.load_fact_store(session)
    return check_gaps(checklist, store)


# --------------------------------------------------------------------------
# Banker review workflow
# --------------------------------------------------------------------------


@app.get("/api/review/state")
async def review_state_view(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReviewState:
    return await review_repo.load_review_state(session)


@app.post("/api/review/{entry_id}/advance")
async def review_advance(
    entry_id: str,
    req: AdvanceRequest,
    _user: User = Depends(require_roles(Role.BANKER)),
    session: AsyncSession = Depends(get_session),
) -> ReviewState:
    """Certification is the one action the problem statement requires stay with
    the merchant banker — the role check here is the whole point of the lock."""
    try:
        await review_repo.advance(session, entry_id, req.to)
    except ValueError as exc:
        # Illegal state transition → 409 Conflict (this is not user error, it's
        # a workflow-order violation, which HTTP models as a conflict).
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await review_repo.load_review_state(session)


@app.post("/api/review/edit")
async def review_edit(
    edit: BankerEdit,
    current_user: User = Depends(require_roles(Role.BANKER)),
    session: AsyncSession = Depends(get_session),
) -> ReviewState:
    """``editor`` is always the authenticated banker's email, not the free-text
    value the client sent — the audit trail should be trustworthy by
    construction, not by convention."""
    await review_repo.record_edit(session, edit.model_copy(update={"editor": current_user.email}))
    return await review_repo.load_review_state(session)


@app.post("/api/review/export")
async def review_export(
    _user: User = Depends(require_roles(Role.PROMOTER, Role.BANKER)),
    session: AsyncSession = Depends(get_session),
) -> ExportResponse:
    """Certification lock: refuse export until every blocker section is certified.

    On success both output targets (DRHP + draft abridged prospectus) are
    assembled into ``out/`` and their download URLs returned. Actual bytes
    are served by ``GET /api/assemble/{target}``.
    """
    review_state = await review_repo.load_review_state(session)
    allowed, blockers = export_allowed(checklist, review_state)
    if not allowed:
        raise HTTPException(status_code=409, detail={"blocked_by": blockers})
    for target in OutputTarget:
        await _assemble_target(session, target)
    return ExportResponse(drhp="/api/assemble/drhp", abridged="/api/assemble/abridged")


# --------------------------------------------------------------------------
# Assembly (on-demand)
# --------------------------------------------------------------------------


_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _target_path(target: OutputTarget) -> Path:
    return OUT_DIR / f"{target.value}.docx"


async def _assemble_target(session: AsyncSession, target: OutputTarget) -> Path:
    """Assemble the given target into ``out/`` (idempotent — overwrites).

    The live store goes along so the cover page can surface a confirmed
    issue-size contradiction as a visible callout in the exported artefact.
    """
    path = _target_path(target)
    store = await facts_repo.load_fact_store(session)
    return assemble(checklist, runtime_cache.get_generated_sections(), target, path, store=store)


@app.get("/api/assemble/{target}")
async def assemble_target(
    target: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Serve an assembled .docx, assembling on demand if not already cached.

    The certification lock only matters for the on-demand path: a file that
    already exists in ``out/`` was necessarily produced by an authorized
    ``POST /api/review/export`` (which already checked the lock), so
    re-serving it is safe. Assembling fresh here — skipping that endpoint
    entirely — must be gated the same way, or the lock would be a UI-only
    formality rather than something the server actually enforces.
    """
    try:
        target_enum = OutputTarget(target)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"unknown target: {target}") from exc
    path = _target_path(target_enum)
    if not path.exists():
        review_state = await review_repo.load_review_state(session)
        allowed, blockers = export_allowed(checklist, review_state)
        if not allowed:
            raise HTTPException(status_code=409, detail={"blocked_by": blockers})
        await _assemble_target(session, target_enum)
    return FileResponse(
        path=str(path),
        media_type=_DOCX_MEDIA_TYPE,
        filename=path.name,
    )


# --------------------------------------------------------------------------
# Exchange-ready bundle export
# --------------------------------------------------------------------------


BUNDLE_FILENAME = "drhp_studio_package.zip"


@app.get("/api/export/bundle")
async def export_bundle(
    _user: User = Depends(require_roles(Role.PROMOTER, Role.BANKER)),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Exchange-ready ZIP: both .docx targets plus the complete audit trail.

    Gated by the same certification lock as ``POST /api/review/export`` —
    the package cannot leave the tool until every blocker-severity section is
    certified (409 with the blocker list until then). Every validation payload
    (gaps, contradictions, coverage, examiner objections, arithmetic findings)
    is computed fresh here so the bundle reflects the store as exported, never
    a stale cache.
    """
    review_state = await review_repo.load_review_state(session)
    allowed, blockers = export_allowed(checklist, review_state)
    if not allowed:
        raise HTTPException(status_code=409, detail={"blocked_by": blockers})

    store = await facts_repo.load_fact_store(session)
    drhp_path = await _assemble_target(session, OutputTarget.DRHP)
    abridged_path = await _assemble_target(session, OutputTarget.ABRIDGED)
    contradictions = await _current_contradictions(store)
    arithmetic = check_arithmetic(store)
    bundle_path = build_bundle(
        checklist=checklist,
        sections=runtime_cache.get_generated_sections(),
        store=store,
        review_state=review_state,
        gaps=check_gaps(checklist, store),
        contradictions=contradictions,
        coverage=score(checklist, runtime_cache.get_generated_sections(), store=store),
        objections=await _examiner_objections(store, contradictions, arithmetic),
        arithmetic=arithmetic,
        drhp_path=drhp_path,
        abridged_path=abridged_path,
        out_path=OUT_DIR / BUNDLE_FILENAME,
    )
    return FileResponse(
        path=str(bundle_path),
        media_type="application/zip",
        filename=BUNDLE_FILENAME,
    )


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------


@app.get("/api/audit")
async def view_audit_log(
    actor_email: str | None = Query(default=None, max_length=254),
    action: str | None = Query(default=None, max_length=100),
    resource_type: str | None = Query(default=None, max_length=50),
    outcome: Outcome | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    _user: User = Depends(require_roles(Role.BANKER)),
) -> list[AuditEvent]:
    """Compliance review: who accessed or changed what, and when.

    Banker-only — the same intermediary who holds certification authority
    over the draft is the one with oversight over the audit trail, matching
    the certification-lock philosophy everywhere else in this app. Every
    request against the API (bar the health check) is recorded, including
    denied ones, so this also answers "did anyone try something they
    shouldn't have."
    """
    return get_audit_log().list_events(
        actor_email=actor_email,
        action=action,
        resource_type=resource_type,
        outcome=outcome,
        limit=limit,
    )


# --------------------------------------------------------------------------
# Extraction reliability (see app.extraction_reliability)
# --------------------------------------------------------------------------


@app.get("/api/extraction-reliability")
async def extraction_reliability(
    _user: User = Depends(require_roles(Role.BANKER)),
    session: AsyncSession = Depends(get_session),
) -> ExtractionReliabilityReport:
    """How often extracted facts turn out to need correction, broken down by
    source and confidence — and specifically how often a BANKER'S due-
    diligence review is what caught it (as opposed to a self-correction).
    Banker-only, same oversight rationale as the audit log above: this is a
    QA signal about the tool's own reliability, not draft content.
    """
    store = await facts_repo.load_fact_store(session)
    return compute_reliability(store)
