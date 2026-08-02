"""Extraction reliability tracking (app.extraction_reliability): the
banker-correction feedback loop's aggregation logic.

Every scenario here was hand-verified against a standalone script before
being written up as a formal test — the numbers below are the exact ones
that verification produced.
"""

from __future__ import annotations

from app.extraction_reliability import compute_reliability
from app.facts import Fact, FactStore, Provenance, SourceKind
from app.schema.models import Role


def _fact(store: FactStore, **overrides: object) -> Fact:
    defaults: dict[str, object] = {
        "key": "some_key",
        "value": "v",
        "provenance": Provenance(kind=SourceKind.DOCUMENT, detail="extracted"),
        "confidence": 0.9,
        "supplied_by": Role.PROMOTER,
    }
    defaults.update(overrides)
    return store.add(Fact(**defaults))  # type: ignore[arg-type]


def test_empty_store_yields_an_empty_report() -> None:
    report = compute_reliability(FactStore())
    assert report.buckets == []
    assert report.total_extracted_facts == 0
    assert report.total_corrections == 0
    assert report.total_banker_caught_corrections == 0


def test_wizard_facts_are_never_counted_as_extracted() -> None:
    store = FactStore()
    _fact(store, provenance=Provenance(kind=SourceKind.WIZARD, detail="q"), confidence=1.0)
    report = compute_reliability(store)
    assert report.total_extracted_facts == 0
    assert report.buckets == []


def test_document_lookup_and_role_upload_all_count_as_extracted() -> None:
    store = FactStore()
    for kind in (SourceKind.DOCUMENT, SourceKind.LOOKUP, SourceKind.ROLE_UPLOAD):
        _fact(store, key=kind.value, provenance=Provenance(kind=kind, detail="x"))
    report = compute_reliability(store)
    assert report.total_extracted_facts == 3


def test_uncorrected_extracted_fact_has_zero_correction_rate() -> None:
    store = FactStore()
    _fact(store, confidence=0.9)
    report = compute_reliability(store)
    bucket = report.buckets[0]
    assert bucket.total_facts == 1
    assert bucket.corrected_count == 0
    assert bucket.correction_rate == 0.0


def test_low_and_normal_confidence_are_bucketed_separately() -> None:
    store = FactStore()
    _fact(store, key="low", confidence=0.6)
    _fact(store, key="high", confidence=0.9)
    report = compute_reliability(store)
    bands = {b.confidence_band for b in report.buckets}
    assert bands == {"low (<0.7)", "normal (>=0.7)"}
    assert all(b.total_facts == 1 for b in report.buckets)


def test_the_headline_scenario_banker_catches_a_promoter_extraction_error() -> None:
    """The exact scenario this feature exists for: an OCR-extracted,
    promoter-supplied fact gets corrected by a banker during due diligence."""
    store = FactStore()
    original = _fact(store, confidence=0.6, supplied_by=Role.PROMOTER)
    store.confirm(original.fact_id)
    store.correct(
        original.fact_id,
        new_value="corrected",
        provenance=Provenance(kind=SourceKind.DOCUMENT, detail="banker fix"),
        corrected_by_role=Role.BANKER,
    )

    report = compute_reliability(store)
    assert report.total_extracted_facts == 1
    assert report.total_corrections == 1
    assert report.total_banker_caught_corrections == 1
    bucket = report.buckets[0]
    assert bucket.confidence_band == "low (<0.7)"
    assert bucket.corrected_count == 1
    assert bucket.correction_rate == 1.0
    assert bucket.banker_caught_count == 1


def test_self_correction_by_the_original_supplier_is_not_banker_caught() -> None:
    store = FactStore()
    original = _fact(store, confidence=0.9, supplied_by=Role.PROMOTER)
    store.correct(
        original.fact_id,
        new_value="fixed",
        provenance=Provenance(kind=SourceKind.DOCUMENT, detail="self fix"),
        corrected_by_role=Role.PROMOTER,
    )
    report = compute_reliability(store)
    assert report.total_corrections == 1
    assert report.total_banker_caught_corrections == 0


def test_banker_correcting_their_own_supplied_fact_is_not_banker_caught() -> None:
    """A banker fixing a typo in their OWN due-diligence upload is a
    self-correction, not a due-diligence catch of someone else's error —
    the whole point of banker_caught_count is measuring the LATTER."""
    store = FactStore()
    original = _fact(
        store,
        provenance=Provenance(kind=SourceKind.ROLE_UPLOAD, detail="dd cert"),
        confidence=0.6,
        supplied_by=Role.BANKER,
    )
    store.correct(
        original.fact_id,
        new_value="fixed",
        provenance=Provenance(kind=SourceKind.ROLE_UPLOAD, detail="banker self fix"),
        corrected_by_role=Role.BANKER,
    )
    report = compute_reliability(store)
    assert report.total_corrections == 1
    assert report.total_banker_caught_corrections == 0


def test_banker_catch_deep_in_a_correction_chain_still_counts() -> None:
    """original -> promoter self-fix -> banker fix: the banker catch is two
    steps downstream of the original fact, not the immediate correction."""
    store = FactStore()
    original = _fact(store, confidence=0.6, supplied_by=Role.PROMOTER)
    first_fix = store.correct(
        original.fact_id,
        new_value="v2",
        provenance=Provenance(kind=SourceKind.DOCUMENT, detail="self fix"),
        corrected_by_role=Role.PROMOTER,
    )
    store.correct(
        first_fix.fact_id,
        new_value="v3",
        provenance=Provenance(kind=SourceKind.DOCUMENT, detail="banker catches it later"),
        corrected_by_role=Role.BANKER,
    )
    report = compute_reliability(store)
    # Only ONE original extracted fact — the two correction versions aren't
    # separately counted as new extractions.
    assert report.total_extracted_facts == 1
    assert report.total_corrections == 1
    assert report.total_banker_caught_corrections == 1


def test_unconfirmed_and_confirmed_facts_are_both_included() -> None:
    """Unlike generation (confirmed-only), reliability tracking cares about
    every extraction attempt regardless of confirmation status — an
    unconfirmed proposal that got corrected before ever being confirmed is
    still a real extraction-quality data point."""
    store = FactStore()
    _fact(store, confidence=0.6)  # never confirmed
    report = compute_reliability(store)
    assert report.total_extracted_facts == 1


def test_multiple_facts_in_the_same_bucket_aggregate() -> None:
    store = FactStore()
    a = _fact(store, key="a", confidence=0.9, supplied_by=Role.PROMOTER)
    _fact(store, key="b", confidence=0.9, supplied_by=Role.PROMOTER)
    store.correct(
        a.fact_id,
        new_value="fixed",
        provenance=Provenance(kind=SourceKind.DOCUMENT, detail="fix"),
        corrected_by_role=Role.BANKER,
    )
    report = compute_reliability(store)
    assert len(report.buckets) == 1
    bucket = report.buckets[0]
    assert bucket.total_facts == 2
    assert bucket.corrected_count == 1
    assert bucket.correction_rate == 0.5
    assert bucket.banker_caught_count == 1
