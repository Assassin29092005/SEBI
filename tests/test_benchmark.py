"""Reference-DRHP benchmark: real filed TOCs mapped against the checklist schema."""

from __future__ import annotations

from pathlib import Path

from app.coverage import benchmark, load_reference_benchmarks
from app.schema.loader import load_checklist


def test_reference_mappings_load_and_are_chapter_ix_filings() -> None:
    references = load_reference_benchmarks()
    assert len(references) >= 2, "need at least two real filed SME DRHPs as evidence"
    for ref in references:
        assert ref.source_url.startswith("https://")
        assert "Chapter IX" in ref.framework_evidence or "229" in ref.framework_evidence
        assert len(ref.chapters) >= 20  # a real DRHP TOC, not a stub file


def test_every_maps_to_id_exists_in_checklist() -> None:
    """Stale mapping ids must fail loudly here, not silently inflate the score."""
    known_ids = {e.id for e in load_checklist().entries}
    for ref in load_reference_benchmarks():
        for chapter in ref.chapters:
            unknown = [i for i in chapter.maps_to if i not in known_ids]
            assert not unknown, (
                f"{ref.company}: chapter {chapter.heading!r} maps to unknown "
                f"checklist ids {unknown}"
            )


def test_benchmark_summary_reports_honest_counts() -> None:
    report = benchmark(load_checklist())
    assert report.summary, "summary must not be empty when reference files exist"
    for row in report.summary:
        encoded = row["chapters_encoded"]
        auditor = row["chapters_out_of_scope_auditor"]
        not_encoded = row["chapters_not_encoded"]
        assert isinstance(not_encoded, list)
        assert encoded + auditor + len(not_encoded) == row["chapters_total"]
        # The schema is not complete against real filings — the benchmark must
        # say so rather than claim 100%.
        assert 0 < row["in_scope_coverage_pct"] < 100 or not_encoded == []


def test_benchmark_missing_directory_yields_empty_report(tmp_path: Path) -> None:
    report = benchmark(load_checklist(), directory=tmp_path / "nope")
    assert report.references == []
    assert report.summary == []


def test_stale_mapping_id_downgrades_to_not_encoded(tmp_path: Path) -> None:
    (tmp_path / "fake.sections.yaml").write_text(
        """
company: "Test Co"
source_url: "https://example.com/drhp.pdf"
filed: "2026-01-01"
exchange: "NSE Emerge"
framework_evidence: "Chapter IX"
chapters:
  - heading: "Ghost Chapter"
    maps_to: [nonexistent.entry_id]
""",
        encoding="utf-8",
    )
    report = benchmark(load_checklist(), directory=tmp_path)
    chapter = report.references[0].chapters[0]
    assert chapter.maps_to == []
    assert chapter.status == "not_encoded"
