"""Adversarial examiner agent — plays exchange reviewer against the draft.

CUT-LINE FEATURE (day 10–11, first in cut priority): raises objections
section-by-section until the draft survives them. Stub until then.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.generate.sections import GeneratedSection


class Objection(BaseModel):
    entry_id: str
    objection: str
    clause_ref: str | None
    resolved: bool = False


async def examine(sections: list[GeneratedSection]) -> list[Objection]:
    """TODO (cut-line): iterative objection loop via app.llm.client, temp 0."""
    raise NotImplementedError("adversarial examiner: cut-line feature (day 10–11)")
