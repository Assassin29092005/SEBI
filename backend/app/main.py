"""FastAPI app tying the pipeline together.

Demo-grade wiring: a single in-process fact store, review state, and
generated-sections cache. Auth and RBAC are production concerns — documented,
not built. Persistence is demo-grade too: when ``settings.persist_session`` is
on, every mutating endpoint snapshots the session to disk (one JSON file, see
:mod:`app.persistence`) and boot rehydrates from it, so a backend restart
mid-demo does not lose the session.

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

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.assemble.bundle import build_bundle
from app.assemble.docx_builder import assemble
from app.config import settings
from app.coverage import BenchmarkReport, CoverageReport, benchmark, score
from app.eligibility import EligibilityInput, EligibilityReport, evaluate
from app.facts import Fact, FactStore, Provenance
from app.generate.sections import GeneratedSection, generate_all
from app.intake.litigation import LitigationRecord, MockLitigationConnector
from app.intake.uploads import ExtractionProposal, extract_facts, proposal_to_fact
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

app = FastAPI(title="DRHP Studio", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    tests, and deletes the persisted session snapshot — unconditionally, NOT
    gated on ``settings.persist_session``: reset means clean slate, and tests
    rely on a stale snapshot never rehydrating into a later run. Never touches
    the checklist (which stays module-level).
    """
    global state
    state = create_state()
    clear_snapshot()
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR, ignore_errors=True)
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
async def eligibility(data: EligibilityInput) -> EligibilityReport:
    return evaluate(data)


# --------------------------------------------------------------------------
# Wizard
# --------------------------------------------------------------------------


@app.get("/api/wizard/questions")
async def wizard_questions(lang: str = Query(default="en")) -> list[WizardQuestion]:
    return derive_questions(checklist, lang=lang)


# --------------------------------------------------------------------------
# Facts
# --------------------------------------------------------------------------


@app.get("/api/facts")
async def list_facts() -> list[Fact]:
    """Return every fact in the store — confirmed AND unconfirmed.

    The UI needs unconfirmed proposals visible so the promoter can act on them;
    generation still ignores anything not confirmed (see FactStore.confirmed_by_key).
    """
    return list(state.fact_store._facts.values())  # noqa: SLF001 — thin API view


@app.post("/api/facts")
async def add_fact(fact: Fact) -> Fact:
    added = state.fact_store.add(fact)
    _persist()
    return added


@app.post("/api/facts/{fact_id}/confirm")
async def confirm_fact(fact_id: str) -> Fact:
    try:
        confirmed = state.fact_store.confirm(fact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"fact not found: {fact_id}") from exc
    _persist()
    return confirmed


@app.post("/api/facts/{fact_id}/correct")
async def correct_fact(fact_id: str, req: CorrectionRequest) -> Fact:
    try:
        corrected = state.fact_store.correct(fact_id, req.value, req.provenance)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"fact not found: {fact_id}") from exc
    _persist()
    return corrected


# --------------------------------------------------------------------------
# Uploads / extraction / proposals
# --------------------------------------------------------------------------


@app.post("/api/uploads/extract")
async def uploads_extract(
    file: Annotated[UploadFile, File(...)],
) -> list[ExtractionProposal]:
    content = await file.read()
    filename = file.filename or "upload.txt"
    return await extract_facts(filename, content)


@app.post("/api/proposals/accept")
async def proposals_accept(
    proposal: ExtractionProposal, role: Role = Role.PROMOTER
) -> Fact:
    """Materialise a proposal into an unconfirmed Fact in the store.

    Confirmation is a separate step (POST /api/facts/{id}/confirm) — the
    unconfirmed fact never feeds generation. ``role`` tags who supplied the
    document (role-based truth: auditor/banker uploads enter as that role).
    """
    fact = state.fact_store.add(proposal_to_fact(proposal, supplied_by=role))
    _persist()
    return fact


# --------------------------------------------------------------------------
# Litigation lookup
# --------------------------------------------------------------------------


@app.get("/api/litigation")
async def litigation(entity: str = Query(...)) -> list[LitigationRecord]:
    return await state.litigation_connector.search(entity, {})


# --------------------------------------------------------------------------
# Generation + cached sections
# --------------------------------------------------------------------------


@app.post("/api/generate")
async def generate() -> list[GeneratedSection]:
    """Run grounded generation over the current fact store and cache the result.

    Cached under ``state.generated_sections``; readable via ``GET /api/sections``.
    """
    state.generated_sections = await generate_all(checklist, state.fact_store)
    _persist()
    return state.generated_sections


@app.get("/api/sections")
async def sections() -> list[GeneratedSection]:
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
async def validate_contradictions() -> list[Contradiction]:
    return await _current_contradictions()


@app.get("/api/validate/semantic")
async def validate_semantic() -> list[Contradiction]:
    """Free-prose cross-section consistency (LLM enrichment; [] offline)."""
    return await semantic_check(state.generated_sections)


@app.get("/api/validate/boilerplate")
async def validate_boilerplate() -> list[BoilerplateFlag]:
    return _current_boilerplate()


@app.get("/api/validate/arithmetic")
async def validate_arithmetic() -> list[ArithmeticFinding]:
    """Objects-of-the-Issue arithmetic over confirmed facts (deterministic, no LLM)."""
    return check_arithmetic(state.fact_store)


@app.get("/api/validate/examiner")
async def validate_examiner() -> list[Objection]:
    return await _examiner_objections(
        await _current_contradictions(),
        check_arithmetic(state.fact_store),
    )


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


@app.get("/api/coverage")
async def coverage() -> CoverageReport:
    return score(checklist, state.generated_sections, store=state.fact_store)


@app.get("/api/coverage/benchmark")
async def coverage_benchmark() -> BenchmarkReport:
    """Schema coverage of real filed SME DRHP tables of contents (evidence, not a claim)."""
    return benchmark(checklist)


# --------------------------------------------------------------------------
# Gaps
# --------------------------------------------------------------------------


@app.get("/api/gaps")
async def gaps() -> GapReport:
    return check_gaps(checklist, state.fact_store)


# --------------------------------------------------------------------------
# Banker review workflow
# --------------------------------------------------------------------------


@app.get("/api/review/state")
async def review_state_view() -> ReviewState:
    return state.review_state


@app.post("/api/review/{entry_id}/advance")
async def review_advance(entry_id: str, req: AdvanceRequest) -> ReviewState:
    try:
        state.review_state.advance(entry_id, req.to)
    except ValueError as exc:
        # Illegal state transition → 409 Conflict (this is not user error, it's
        # a workflow-order violation, which HTTP models as a conflict).
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _persist()
    return state.review_state


@app.post("/api/review/edit")
async def review_edit(edit: BankerEdit) -> ReviewState:
    state.review_state.record_edit(edit)
    _persist()
    return state.review_state


@app.post("/api/review/export")
async def review_export() -> ExportResponse:
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
async def assemble_target(target: str) -> FileResponse:
    try:
        target_enum = OutputTarget(target)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"unknown target: {target}") from exc
    path = _target_path(target_enum)
    if not path.exists():
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
async def export_bundle() -> FileResponse:
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
