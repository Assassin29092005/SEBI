"""Quantitative coverage score vs. real filed SME DRHPs.

Two independent measurements — judges get evidence, not a claim:

1. :func:`score` — schema coverage: how many encoded checklist requirements
   the generated draft satisfies gap-free. Auditor-only content is explicitly
   marked out-of-scope, never silently counted.
2. :func:`benchmark` — reference-filing coverage: the tables of contents of
   real filed SME DRHPs (public NSE Emerge documents in
   ``data/reference_drhps/*.sections.yaml``, hand-mapped chapter by chapter)
   checked against the checklist schema. This is the external check the
   schema cannot give itself: a chapter every real filing carries but the
   schema lacks shows up as ``not_encoded`` instead of being invisible.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from app.config import settings
from app.facts import FactStore
from app.generate.sections import GeneratedSection
from app.schema.applicability import entry_applies
from app.schema.models import Checklist, Role


class SectionCoverage(BaseModel):
    section: str
    covered: int                 # non-stub entries with generated, gap-free text
    total: int
    out_of_scope: int            # auditor-only entries, excluded from the ratio
    not_applicable: int = 0      # conditional entries whose has_* condition is unmet


class CoverageReport(BaseModel):
    sections: list[SectionCoverage]

    @property
    def overall_pct(self) -> float:
        covered = sum(s.covered for s in self.sections)
        total = sum(s.total for s in self.sections)
        return 100.0 * covered / total if total else 0.0


def score(
    checklist: Checklist,
    sections: list[GeneratedSection],
    store: FactStore | None = None,
) -> CoverageReport:
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
    - **Inapplicable conditional entries count only toward ``not_applicable``**
      (when a ``store`` is given). An unmet ``has_*`` condition means the
      regulation does not require the disclosure of this issuer — counting it
      as uncovered would penalise honesty. Without a store, conditional
      entries stay in ``total`` (the conservative reading).
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
        non_auditor = [e for e in encoded if e.responsible_role != Role.AUDITOR]
        if store is not None:
            in_scope = [e for e in non_auditor if entry_applies(e, store)]
        else:
            in_scope = non_auditor
        report_sections.append(
            SectionCoverage(
                section=section_name,
                covered=sum(1 for e in in_scope if e.id in gap_free_entry_ids),
                total=len(in_scope),
                out_of_scope=len(encoded) - len(non_auditor),
                not_applicable=len(non_auditor) - len(in_scope),
            )
        )
    return CoverageReport(sections=report_sections)


# --------------------------------------------------------------------------
# Reference-filing benchmark (data/reference_drhps/*.sections.yaml)
# --------------------------------------------------------------------------

REFERENCE_SECTIONS_DIR: Path = settings.data_dir / "reference_drhps"


class ChapterMapping(BaseModel):
    heading: str                        # chapter heading as it appears in the filed TOC
    maps_to: list[str] = []             # checklist entry ids covering this chapter
    status: str = "encoded"             # encoded | out_of_scope_auditor | not_encoded
    note: str | None = None


class ReferenceBenchmark(BaseModel):
    company: str
    source_url: str
    filed: str                          # ISO date the DRHP was filed
    exchange: str
    framework_evidence: str             # why this filing is a Chapter IX comparable
    chapters: list[ChapterMapping]

    @property
    def encoded(self) -> int:
        return sum(1 for c in self.chapters if c.maps_to)

    @property
    def out_of_scope(self) -> int:
        return sum(1 for c in self.chapters if not c.maps_to and c.status == "out_of_scope_auditor")

    @property
    def not_encoded(self) -> list[str]:
        return [
            c.heading
            for c in self.chapters
            if not c.maps_to and c.status != "out_of_scope_auditor"
        ]

    @property
    def in_scope_pct(self) -> float:
        in_scope = len(self.chapters) - self.out_of_scope
        return 100.0 * self.encoded / in_scope if in_scope else 0.0


class BenchmarkReport(BaseModel):
    references: list[ReferenceBenchmark]
    # Serialised summary (properties are not serialised by Pydantic):
    summary: list[dict[str, object]] = []


def load_reference_benchmarks(directory: Path | None = None) -> list[ReferenceBenchmark]:
    """Load every ``*.sections.yaml`` reference mapping; missing dir → empty list."""
    base = directory if directory is not None else REFERENCE_SECTIONS_DIR
    if not base.exists():
        return []
    references: list[ReferenceBenchmark] = []
    for path in sorted(base.glob("*.sections.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        references.append(ReferenceBenchmark.model_validate(raw))
    return references


def benchmark(checklist: Checklist, directory: Path | None = None) -> BenchmarkReport:
    """Check the schema against real filed DRHP tables of contents.

    A chapter mapping to an entry id absent from the checklist is downgraded
    to unmapped — the benchmark must never overstate coverage because a
    mapping file went stale after a schema change.
    """
    known_ids = {entry.id for entry in checklist.entries if not entry.stub}
    references = load_reference_benchmarks(directory)
    for ref in references:
        for chapter in ref.chapters:
            chapter.maps_to = [i for i in chapter.maps_to if i in known_ids]
            if not chapter.maps_to and chapter.status == "encoded":
                chapter.status = "not_encoded"
    summary: list[dict[str, object]] = [
        {
            "company": ref.company,
            "filed": ref.filed,
            "chapters_total": len(ref.chapters),
            "chapters_encoded": ref.encoded,
            "chapters_out_of_scope_auditor": ref.out_of_scope,
            "chapters_not_encoded": ref.not_encoded,
            "in_scope_coverage_pct": round(ref.in_scope_pct, 1),
        }
        for ref in references
    ]
    return BenchmarkReport(references=references, summary=summary)
