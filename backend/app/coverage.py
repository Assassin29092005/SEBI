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
    """Compute per-section coverage of the checklist by the generated sections.

    Counting rules (in order):

    - **Stub entries are excluded entirely.** A stub exists in the schema for
      structural accounting only — the requirement is not yet encoded. Counting
      a stub toward ``total`` would deflate the score for content we never
      attempted; counting it toward ``covered`` would inflate it. Either way the
      number lies, so stubs appear on neither side of the ratio.
    - **Auditor entries count only toward ``out_of_scope``.** Restated
      financials and other auditor content can, by law, only be produced by a
      peer-reviewed auditor — the tool ingests and formats such content but
      never generates it. Per CLAUDE.md this is stated openly rather than
      silently counted, so auditor entries never touch ``total`` or ``covered``
      (even if a generated section happens to exist for one).
    - Everything else is in scope: ``total`` counts the non-stub, non-auditor
      entries; ``covered`` counts those with a :class:`GeneratedSection` present
      (matched by ``entry_id``) whose ``missing_facts`` is empty — a section
      still carrying ``[REQUIRES INPUT]`` gaps is honest, but it is not covered.

    Sections whose entries are all stubs are omitted from the report: an
    all-zero row would be noise, not evidence.
    """
    gap_free_entry_ids = {s.entry_id for s in sections if not s.missing_facts}

    report_sections: list[SectionCoverage] = []
    for section_name, entries in checklist.by_section().items():
        encoded = [e for e in entries if not e.stub]
        if not encoded:
            continue  # nothing in this section is encoded yet — no evidence either way
        in_scope = [e for e in encoded if e.responsible_role != Role.AUDITOR]
        report_sections.append(
            SectionCoverage(
                section=section_name,
                covered=sum(1 for e in in_scope if e.id in gap_free_entry_ids),
                total=len(in_scope),
                out_of_scope=len(encoded) - len(in_scope),
            )
        )
    return CoverageReport(sections=report_sections)
