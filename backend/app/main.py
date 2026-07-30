"""FastAPI app tying the pipeline together.

A single in-process fact store, review state, and generated-sections cache
(single-tenant: one deployment serves one issuer's promoter/auditor/banker
team — see ``app.auth`` for why that's the right unit of isolation here).
Every endpoint below requires a valid JWT bearer token (``app.auth.dependencies
.get_current_user``); actions scoped to one role (promoter confirms facts,
banker certifies sections) additionally depend on ``require_roles(...)`` —
the frontend's old role dropdown was UI-only, this is server-enforced.
Persistence is demo-grade in shape (a snapshot file, not a database) but
real in one respect that matters: when ``settings.persist_session`` is on,
every mutating endpoint snapshots the session to disk, **encrypted at rest**
(see :mod:`app.crypto`), and boot rehydrates from it, so a backend restart
mid-demo does not lose the session. (User accounts persist separately — see
:mod:`app.auth.store` — and are unaffected by a session reset.) Uploaded
source documents are also archived encrypted (see :mod:`app.intake.vault`)
so a banker/auditor can retrieve the original a fact's snippet came from.

State layout
------------
The checklist is loaded once at import time (it is the versioned schema — a
module-level constant). Everything else — the fact store, review state, the
last generated sections, and any assembled files — lives inside a mutable
:class:`AppState` produced by :func:`create_state`. Tests call
:func:`reset_state` (via fixture) to get a clean slate between cases without
re-importing the module.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from app.assemble.bundle import build_bundle
from app.assemble.docx_builder import assemble
from app.auth.dependencies import get_current_user, require_roles
from app.auth.models import User
from app.auth.router import router as auth_router
from app.config import settings
from app.coverage import BenchmarkReport, CoverageReport, benchmark, score
from app.eligibility import EligibilityInput, EligibilityReport, evaluate
from app.facts import Fact, FactStore, Provenance
from app.generate.sections import GeneratedSection, generate_all
from app.intake.litigation import LitigationRecord, MockLitigationConnector
from app.intake.uploads import ExtractionProposal, extract_facts, proposal_to_fact
from app.intake.vault import (
    ArchivedDocumentMeta,
    archive_upload,
    list_archived_uploads,
    retrieve_upload,
)
from app.intake.wizard import WizardQuestion, derive_questions
from app.persistence import clear_snapshot, load_snapshot, restore_fact_store, save_snapshot
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


app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

# Assembled .docx files land here; the directory is created on demand and is
# gitignored (see .gitignore).
OUT_DIR: Path = Path(__file__).resolve().parents[2] / "out"

# The checklist is the versioned schema — module-level, load once.
checklist: Checklist = load_checklist()


# --------------------------------------------------------------------------
# Mutable app state (reset-able for tests)
# --------------------------------------------------------------------------


@dataclass
class AppState:
    """Everything mutable during a single demo run.

    Kept in one dataclass so ``reset_state()`` can swap it atomically — tests
    call it in a fixture to start each case from a clean slate without having
    to reload the checklist YAML.
    """

    fact_store: FactStore = field(default_factory=FactStore)
    review_state: ReviewState = field(default_factory=ReviewState)
    generated_sections: list[GeneratedSection] = field(default_factory=list)
    litigation_connector: MockLitigationConnector = field(default_factory=MockLitigationConnector)


def create_state() -> AppState:
    return AppState()


def restore_persisted_state(target: AppState) -> None:
    """Rehydrate ``target`` from the on-disk session snapshot, if enabled and present.

    Boot-time only (called once at import, and by tests simulating a restart):
    keeps a backend restart mid-demo from losing the session. ``create_state()``
    stays a pure fresh-state factory so :func:`reset_state` semantics (clean
    slate for tests) are untouched. The litigation connector is not restored —
    it is a stateless mock recreated by ``create_state()``.
    """
    if not settings.persist_session:
        return
    snapshot = load_snapshot()
    if snapshot is None:
        return
    target.fact_store = restore_fact_store(snapshot.facts)
    target.review_state = snapshot.review_state
    target.generated_sections = list(snapshot.generated_sections)


state: AppState = create_state()
restore_persisted_state(state)


def reset_state() -> AppState:
    """Swap in a fresh :class:`AppState` and clear any assembled files.

    Called from the test fixture before every case. Also nukes the ``out/``
    directory so ``/api/assemble/{target}`` cases can't leak files between
    tests, deletes the persisted session snapshot — unconditionally, NOT
    gated on ``settings.persist_session``: reset means clean slate, and tests
    rely on a stale snapshot never rehydrating into a later run — and clears
    the archived-uploads vault for the same reason. Never touches the
    checklist (which stays module-level) or user accounts (a session reset
    is not an account wipe — see ``app.auth.store.reset_user_store`` for that).
    """
    global state
    state = create_state()
    clear_snapshot()
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR, ignore_errors=True)
    if settings.uploads_dir.exists():
        shutil.rmtree(settings.uploads_dir, ignore_errors=True)
    return state


def _persist() -> None:
    """Snapshot the session to disk after a mutating endpoint (no-op when disabled).

    Saves ALL facts — unconfirmed proposals and superseded versions included —
    so a restart never silently drops a pending proposal or the audit trail.
    Confirmation status survives verbatim; generation still consumes confirmed
    facts only (that rule lives in the fact store, not here).
    """
    if not settings.persist_session:
        return
    save_snapshot(
        state.fact_store.all_facts(),
        state.review_state,
        state.generated_sections,
    )


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
async def list_facts(_user: User = Depends(get_current_user)) -> list[Fact]:
    """Return every fact in the store — confirmed AND unconfirmed.

    The UI needs unconfirmed proposals visible so the promoter can act on them;
    generation still ignores anything not confirmed (see FactStore.confirmed_by_key).
    """
    return list(state.fact_store._facts.values())  # noqa: SLF001 — thin API view


@app.post("/api/facts")
async def add_fact(
    fact: Fact,
    current_user: User = Depends(require_roles(Role.PROMOTER, Role.AUDITOR, Role.BANKER)),
) -> Fact:
    """Add a fact. ``supplied_by`` is always the caller's authenticated role —
    role-based truth (who may lawfully supply which content) would be
    meaningless if a client could just set this field itself."""
    added = state.fact_store.add(fact.model_copy(update={"supplied_by": current_user.role}))
    _persist()
    return added


@app.post("/api/facts/{fact_id}/confirm")
async def confirm_fact(
    fact_id: str, _user: User = Depends(require_roles(Role.PROMOTER))
) -> Fact:
    try:
        confirmed = state.fact_store.confirm(fact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"fact not found: {fact_id}") from exc
    _persist()
    return confirmed


@app.post("/api/facts/{fact_id}/correct")
async def correct_fact(
    fact_id: str, req: CorrectionRequest, _user: User = Depends(require_roles(Role.PROMOTER))
) -> Fact:
    try:
        corrected = state.fact_store.correct(fact_id, req.value, req.provenance)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"fact not found: {fact_id}") from exc
    _persist()
    return corrected


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
) -> Fact:
    """Materialise a proposal into an unconfirmed Fact in the store.

    Confirmation is a separate step (POST /api/facts/{id}/confirm) — the
    unconfirmed fact never feeds generation. The supplying role (role-based
    truth: auditor/banker uploads enter as that role) is the caller's
    authenticated role — previously a client-supplied query param, which let
    anyone tag their upload as coming from any role they liked.
    """
    fact = state.fact_store.add(proposal_to_fact(proposal, supplied_by=current_user.role))
    _persist()
    return fact


# --------------------------------------------------------------------------
# Litigation lookup
# --------------------------------------------------------------------------


@app.get("/api/litigation")
async def litigation(
    entity: str = Query(..., min_length=1, max_length=200),
    _user: User = Depends(get_current_user),
) -> list[LitigationRecord]:
    return await state.litigation_connector.search(entity, {})


# --------------------------------------------------------------------------
# Generation + cached sections
# --------------------------------------------------------------------------


@app.post("/api/generate")
async def generate(_user: User = Depends(require_roles(Role.PROMOTER))) -> list[GeneratedSection]:
    """Run grounded generation over the current fact store and cache the result.

    Cached under ``state.generated_sections``; readable via ``GET /api/sections``.
    """
    state.generated_sections = await generate_all(checklist, state.fact_store)
    _persist()
    return state.generated_sections


@app.get("/api/sections")
async def sections(_user: User = Depends(get_current_user)) -> list[GeneratedSection]:
    """Return the last generated sections (empty list if never generated)."""
    return state.generated_sections


# --------------------------------------------------------------------------
# Validation (runs over cached sections)
# --------------------------------------------------------------------------


async def _current_contradictions() -> list[Contradiction]:
    """Cross-check numeric/entity claims across the cached sections + store."""
    all_claims: list[Claim] = []
    for section in state.generated_sections:
        all_claims.extend(await extract_claims(section, state.fact_store))
    return cross_check(all_claims)


def _current_boilerplate() -> list[BoilerplateFlag]:
    flags: list[BoilerplateFlag] = []
    for section in state.generated_sections:
        flags.extend(detect(section))
    return flags


async def _examiner_objections(
    contradictions: list[Contradiction],
    arithmetic: list[ArithmeticFinding],
) -> list[Objection]:
    """Enriched examiner: the other validators' outputs feed its deterministic pass."""
    return await examine(
        state.generated_sections,
        checklist=checklist,
        contradictions=contradictions,
        boilerplate_flags=_current_boilerplate(),
        arithmetic_findings=arithmetic,
        store=state.fact_store,
    )


@app.get("/api/validate/contradictions")
async def validate_contradictions(_user: User = Depends(get_current_user)) -> list[Contradiction]:
    return await _current_contradictions()


@app.get("/api/validate/semantic")
async def validate_semantic(_user: User = Depends(get_current_user)) -> list[Contradiction]:
    """Free-prose cross-section consistency (LLM enrichment; [] offline)."""
    return await semantic_check(state.generated_sections)


@app.get("/api/validate/boilerplate")
async def validate_boilerplate(_user: User = Depends(get_current_user)) -> list[BoilerplateFlag]:
    return _current_boilerplate()


@app.get("/api/validate/arithmetic")
async def validate_arithmetic(_user: User = Depends(get_current_user)) -> list[ArithmeticFinding]:
    """Objects-of-the-Issue arithmetic over confirmed facts (deterministic, no LLM)."""
    return check_arithmetic(state.fact_store)


@app.get("/api/validate/examiner")
async def validate_examiner(_user: User = Depends(get_current_user)) -> list[Objection]:
    return await _examiner_objections(
        await _current_contradictions(),
        check_arithmetic(state.fact_store),
    )


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


@app.get("/api/coverage")
async def coverage(_user: User = Depends(get_current_user)) -> CoverageReport:
    return score(checklist, state.generated_sections, store=state.fact_store)


@app.get("/api/coverage/benchmark")
async def coverage_benchmark(_user: User = Depends(get_current_user)) -> BenchmarkReport:
    """Schema coverage of real filed SME DRHP tables of contents (evidence, not a claim)."""
    return benchmark(checklist)


# --------------------------------------------------------------------------
# Gaps
# --------------------------------------------------------------------------


@app.get("/api/gaps")
async def gaps(_user: User = Depends(get_current_user)) -> GapReport:
    return check_gaps(checklist, state.fact_store)


# --------------------------------------------------------------------------
# Banker review workflow
# --------------------------------------------------------------------------


@app.get("/api/review/state")
async def review_state_view(_user: User = Depends(get_current_user)) -> ReviewState:
    return state.review_state


@app.post("/api/review/{entry_id}/advance")
async def review_advance(
    entry_id: str, req: AdvanceRequest, _user: User = Depends(require_roles(Role.BANKER))
) -> ReviewState:
    """Certification is the one action the problem statement requires stay with
    the merchant banker — the role check here is the whole point of the lock."""
    try:
        state.review_state.advance(entry_id, req.to)
    except ValueError as exc:
        # Illegal state transition → 409 Conflict (this is not user error, it's
        # a workflow-order violation, which HTTP models as a conflict).
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _persist()
    return state.review_state


@app.post("/api/review/edit")
async def review_edit(
    edit: BankerEdit, current_user: User = Depends(require_roles(Role.BANKER))
) -> ReviewState:
    """``editor`` is always the authenticated banker's email, not the free-text
    value the client sent — the audit trail should be trustworthy by
    construction, not by convention."""
    state.review_state.record_edit(edit.model_copy(update={"editor": current_user.email}))
    _persist()
    return state.review_state


@app.post("/api/review/export")
async def review_export(
    _user: User = Depends(require_roles(Role.PROMOTER, Role.BANKER)),
) -> ExportResponse:
    """Certification lock: refuse export until every blocker section is certified.

    On success both output targets (DRHP + draft abridged prospectus) are
    assembled into ``out/`` and their download URLs returned. Actual bytes
    are served by ``GET /api/assemble/{target}``.
    """
    allowed, blockers = export_allowed(checklist, state.review_state)
    if not allowed:
        raise HTTPException(status_code=409, detail={"blocked_by": blockers})
    for target in OutputTarget:
        _assemble_target(target)
    return ExportResponse(drhp="/api/assemble/drhp", abridged="/api/assemble/abridged")


# --------------------------------------------------------------------------
# Assembly (on-demand)
# --------------------------------------------------------------------------


_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _target_path(target: OutputTarget) -> Path:
    return OUT_DIR / f"{target.value}.docx"


def _assemble_target(target: OutputTarget) -> Path:
    """Assemble the given target into ``out/`` (idempotent — overwrites).

    The live store goes along so the cover page can surface a confirmed
    issue-size contradiction as a visible callout in the exported artefact.
    """
    path = _target_path(target)
    return assemble(checklist, state.generated_sections, target, path, store=state.fact_store)


@app.get("/api/assemble/{target}")
async def assemble_target(
    target: str, _user: User = Depends(get_current_user)
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
        allowed, blockers = export_allowed(checklist, state.review_state)
        if not allowed:
            raise HTTPException(status_code=409, detail={"blocked_by": blockers})
        _assemble_target(target_enum)
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
) -> FileResponse:
    """Exchange-ready ZIP: both .docx targets plus the complete audit trail.

    Gated by the same certification lock as ``POST /api/review/export`` —
    the package cannot leave the tool until every blocker-severity section is
    certified (409 with the blocker list until then). Every validation payload
    (gaps, contradictions, coverage, examiner objections, arithmetic findings)
    is computed fresh here so the bundle reflects the store as exported, never
    a stale cache.
    """
    allowed, blockers = export_allowed(checklist, state.review_state)
    if not allowed:
        raise HTTPException(status_code=409, detail={"blocked_by": blockers})

    drhp_path = _assemble_target(OutputTarget.DRHP)
    abridged_path = _assemble_target(OutputTarget.ABRIDGED)
    contradictions = await _current_contradictions()
    arithmetic = check_arithmetic(state.fact_store)
    bundle_path = build_bundle(
        checklist=checklist,
        sections=state.generated_sections,
        store=state.fact_store,
        review_state=state.review_state,
        gaps=check_gaps(checklist, state.fact_store),
        contradictions=contradictions,
        coverage=score(checklist, state.generated_sections, store=state.fact_store),
        objections=await _examiner_objections(contradictions, arithmetic),
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
