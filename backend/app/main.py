"""FastAPI app tying the pipeline together.

Demo-grade wiring: a single in-process fact store, review state, and
generated-sections cache. Real persistence, auth, and RBAC are production
concerns — documented, not built.

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

from app.assemble.docx_builder import assemble
from app.coverage import CoverageReport, score
from app.eligibility import EligibilityInput, EligibilityReport, evaluate
from app.facts import Fact, FactStore, Provenance
from app.generate.sections import GeneratedSection, generate_all
from app.intake.litigation import LitigationRecord, MockLitigationConnector
from app.intake.uploads import ExtractionProposal, extract_facts, proposal_to_fact
from app.intake.wizard import WizardQuestion, derive_questions
from app.review.workflow import BankerEdit, ReviewState, SectionState, export_allowed
from app.schema.loader import load_checklist
from app.schema.models import Checklist, OutputTarget
from app.validate.boilerplate import BoilerplateFlag, detect
from app.validate.contradictions import Claim, Contradiction, cross_check, extract_claims
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


state: AppState = create_state()


def reset_state() -> AppState:
    """Swap in a fresh :class:`AppState` and clear any assembled files.

    Called from the test fixture before every case. Also nukes the ``out/``
    directory so ``/api/assemble/{target}`` cases can't leak files between
    tests. Never touches the checklist (which stays module-level).
    """
    global state
    state = create_state()
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR, ignore_errors=True)
    return state


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
    return state.fact_store.add(fact)


@app.post("/api/facts/{fact_id}/confirm")
async def confirm_fact(fact_id: str) -> Fact:
    try:
        return state.fact_store.confirm(fact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"fact not found: {fact_id}") from exc


@app.post("/api/facts/{fact_id}/correct")
async def correct_fact(fact_id: str, req: CorrectionRequest) -> Fact:
    try:
        return state.fact_store.correct(fact_id, req.value, req.provenance)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"fact not found: {fact_id}") from exc


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
async def proposals_accept(proposal: ExtractionProposal) -> Fact:
    """Materialise a proposal into an unconfirmed Fact in the store.

    Confirmation is a separate step (POST /api/facts/{id}/confirm) — the
    unconfirmed fact never feeds generation.
    """
    return state.fact_store.add(proposal_to_fact(proposal))


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
    return state.generated_sections


@app.get("/api/sections")
async def sections() -> list[GeneratedSection]:
    """Return the last generated sections (empty list if never generated)."""
    return state.generated_sections


# --------------------------------------------------------------------------
# Validation (runs over cached sections)
# --------------------------------------------------------------------------


@app.get("/api/validate/contradictions")
async def validate_contradictions() -> list[Contradiction]:
    all_claims: list[Claim] = []
    for section in state.generated_sections:
        all_claims.extend(await extract_claims(section, state.fact_store))
    return cross_check(all_claims)


@app.get("/api/validate/boilerplate")
async def validate_boilerplate() -> list[BoilerplateFlag]:
    flags: list[BoilerplateFlag] = []
    for section in state.generated_sections:
        flags.extend(detect(section))
    return flags


@app.get("/api/validate/examiner")
async def validate_examiner() -> list[Objection]:
    return await examine(state.generated_sections, checklist=checklist)


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


@app.get("/api/coverage")
async def coverage() -> CoverageReport:
    return score(checklist, state.generated_sections)


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
    return state.review_state


@app.post("/api/review/edit")
async def review_edit(edit: BankerEdit) -> ReviewState:
    state.review_state.record_edit(edit)
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
    """Assemble the given target into ``out/`` (idempotent — overwrites)."""
    path = _target_path(target)
    return assemble(checklist, state.generated_sections, target, path)


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
