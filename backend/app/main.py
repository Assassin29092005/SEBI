"""FastAPI app tying the pipeline together.

Demo-grade wiring: a single in-process fact store and review state. Real
persistence, auth, and RBAC are production concerns — documented, not built.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.eligibility import EligibilityInput, EligibilityReport, evaluate
from app.facts import Fact, FactStore
from app.intake.wizard import WizardQuestion, derive_questions
from app.review.workflow import ReviewState
from app.schema.loader import load_checklist
from app.schema.models import Checklist
from app.validate.gaps import GapReport, check_gaps

app = FastAPI(title="DRHP Studio", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

# Demo-grade singletons.
checklist: Checklist = load_checklist()
fact_store = FactStore()
review_state = ReviewState()


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "schema_version": checklist.header.schema_version}


@app.get("/api/schema")
async def get_schema() -> Checklist:
    return checklist


@app.post("/api/eligibility")
async def eligibility(data: EligibilityInput) -> EligibilityReport:
    return evaluate(data)


@app.get("/api/wizard/questions")
async def wizard_questions() -> list[WizardQuestion]:
    return derive_questions(checklist)


@app.post("/api/facts")
async def add_fact(fact: Fact) -> Fact:
    return fact_store.add(fact)


@app.post("/api/facts/{fact_id}/confirm")
async def confirm_fact(fact_id: str) -> Fact:
    return fact_store.confirm(fact_id)


@app.get("/api/gaps")
async def gaps() -> GapReport:
    return check_gaps(checklist, fact_store)


# TODO (day 5–7): /api/generate, /api/validate/contradictions, /api/assemble
# TODO (day 8–9): /api/review endpoints (advance state, edits, export w/ cert lock)
