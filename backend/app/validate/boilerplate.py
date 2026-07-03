"""Boilerplate detector.

Flags generated text that is generic filler or too close to reference-DRHP
phrasing (reference filings are benchmarks, never templates to copy from).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.generate.sections import GeneratedSection


class BoilerplateFlag(BaseModel):
    entry_id: str
    text_span: tuple[int, int]
    reason: str                  # "generic filler" | "near-duplicate of reference DRHP"


def detect(section: GeneratedSection) -> list[BoilerplateFlag]:
    """TODO (day 5–6): n-gram overlap vs. data/reference_drhps/ + generic-phrase list."""
    raise NotImplementedError("boilerplate detection: day 5–6 deliverable")
