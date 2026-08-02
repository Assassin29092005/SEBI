"""Fact-store invariants: immutability, confirmation gating, correction versioning."""

import pytest

from app.facts import Fact, FactStore, Provenance, SourceKind
from app.schema.models import Role


def make_fact(key: str = "issue_size_paise", value: object = 500_000_000_00) -> Fact:
    return Fact(
        key=key,
        value=value,
        provenance=Provenance(kind=SourceKind.WIZARD, detail="q:issue_size"),
        supplied_by=Role.PROMOTER,
    )


def test_facts_are_frozen() -> None:
    fact = make_fact()
    with pytest.raises(Exception):
        fact.value = 0  # type: ignore[misc]


def test_unconfirmed_facts_never_feed_generation() -> None:
    store = FactStore()
    store.add(make_fact())
    assert store.confirmed_by_key("issue_size_paise") == []


def test_confirmation_makes_fact_available() -> None:
    store = FactStore()
    fact = store.add(make_fact())
    store.confirm(fact.fact_id)
    assert len(store.confirmed_by_key("issue_size_paise")) == 1


def test_correction_supersedes_old_version() -> None:
    store = FactStore()
    original = store.add(make_fact(value=100))
    store.confirm(original.fact_id)

    replacement = store.correct(
        original.fact_id,
        new_value=200,
        provenance=Provenance(kind=SourceKind.WIZARD, detail="q:issue_size (corrected)"),
    )
    store.confirm(replacement.fact_id)

    live = store.confirmed_by_key("issue_size_paise")
    assert [f.fact_id for f in live] == [replacement.fact_id]
    assert replacement.provenance.supersedes == original.fact_id


def test_uncorrected_fact_has_no_corrected_by_role() -> None:
    fact = make_fact()
    assert fact.corrected_by_role is None


def test_correction_records_who_performed_it() -> None:
    store = FactStore()
    original = store.add(make_fact(value=100))
    replacement = store.correct(
        original.fact_id,
        new_value=200,
        provenance=Provenance(kind=SourceKind.WIZARD, detail="banker due-diligence fix"),
        corrected_by_role=Role.BANKER,
    )
    assert replacement.corrected_by_role == Role.BANKER
    # supplied_by is untouched — it still means "whose role vouches for this
    # value", preserved unchanged across a correction, separate from who
    # actually performed this particular correction.
    assert replacement.supplied_by == Role.PROMOTER


def test_correction_without_corrected_by_role_defaults_to_none() -> None:
    """Callers that don't care (most of this test file) get the old
    behaviour — corrected_by_role stays unset, not a fabricated guess."""
    store = FactStore()
    original = store.add(make_fact())
    replacement = store.correct(
        original.fact_id, new_value=999, provenance=Provenance(kind=SourceKind.WIZARD, detail="x")
    )
    assert replacement.corrected_by_role is None
