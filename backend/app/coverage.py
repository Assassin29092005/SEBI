"""Quantitative coverage score vs. real filed SME DRHPs.

Completeness is measured per-section against reference filings in
data/reference_drhps/. Auditor-only content is explicitly marked
out-of-scope — never silently counted. Judges get evidence, not a claim.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.generate.sections import GeneratedSection
from app.schema.models import Checklist, Role


class SectionCoverage(BaseModel):
    section: str
    covered: int                 # non-stub entries with generated, gap-free text
    total: int
    out_of_scope: int            # auditor-only entries, excluded from the ratio


class CoverageReport(BaseModel):
    sections: list[SectionCoverage]

    @property
    def overall_pct(self) -> float:
        covered = sum(s.covered for s in self.sections)
        total = sum(s.total for s in self.sections)
        return 100.0 * covered / total if total else 0.0


def score(checklist: Checklist, sections: list[GeneratedSection]) -> CoverageReport:
    """TODO (day 8–9): per-section comparison against reference DRHP structure;
    entries with responsible_role == Role.AUDITOR count as out_of_scope."""
    raise NotImplementedError("coverage score: day 8–9 deliverable")
