"""Session-persistence snapshot: round-trip fidelity, atomicity, corruption safety."""

from pathlib import Path

from app.facts import Fact, FactStore, Provenance, SourceKind
from app.generate.sections import Citation, GeneratedSection
from app.persistence import (
    SNAPSHOT_FILENAME,
    SessionSnapshot,
    clear_snapshot,
    load_snapshot,
    restore_fact_store,
    save_snapshot,
)
from app.review.workflow import BankerEdit, ReviewState, SectionState
from app.schema.models import Role


def _make_fact(key: str, value: object) -> Fact:
    return Fact(
        key=key,
        value=value,
        provenance=Provenance(kind=SourceKind.WIZARD, detail=f"q:{key}"),
        supplied_by=Role.PROMOTER,
    )


def _populated_state() -> tuple[FactStore, list[Fact], ReviewState, list[GeneratedSection]]:
    """A store exercising every persistence-relevant fact shape.

    Facts: one plain confirmed fact, one confirmed-then-corrected chain
    (original + confirmed superseding replacement), and one left unconfirmed.
    """
    store = FactStore()

    issuer = store.add(_make_fact("issuer_name", "Sunrise Agrotech Ltd"))
    issuer = store.confirm(issuer.fact_id)

    original = store.add(_make_fact("issue_size_paise", 100_000_000_00))
    original = store.confirm(original.fact_id)
    replacement = store.correct(
        original.fact_id,
        new_value=120_000_000_00,
        provenance=Provenance(kind=SourceKind.WIZARD, detail="q:issue_size_paise (corrected)"),
    )
    replacement = store.confirm(replacement.fact_id)

    pending = store.add(_make_fact("board_size", 6))  # never confirmed

    facts = [issuer, original, replacement, pending]

    review = ReviewState()
    review.advance("capital_structure.share_capital_history", SectionState.REVIEWED)
    review.advance("capital_structure.share_capital_history", SectionState.CERTIFIED)
    review.advance("general.definitions_abbreviations", SectionState.REVIEWED)
    review.record_edit(
        BankerEdit(
            entry_id="objects.use_of_proceeds",
            editor="demo-banker",
            before="old wording",
            after="new wording",
        )
    )

    section = GeneratedSection(
        entry_id="capital_structure.share_capital_history",
        section="Capital Structure",
        text="Issuer name: Sunrise Agrotech Ltd (source: q:issuer_name).",
        citations=[Citation(fact_id=issuer.fact_id, text_span=(0, 58))],
        missing_facts=["share_allotments[]"],
    )

    return store, facts, review, [section]


def test_round_trip_preserves_everything(tmp_path: Path) -> None:
    _, facts, review, sections = _populated_state()

    path = save_snapshot(facts, review, sections, directory=tmp_path)
    assert path == tmp_path / SNAPSHOT_FILENAME
    assert path.exists()

    loaded = load_snapshot(directory=tmp_path)
    assert loaded is not None
    assert loaded.facts == facts
    assert loaded.review_state == review
    assert loaded.generated_sections == sections
    assert loaded.saved_at  # ISO timestamp recorded

    # citation span survives JSON (list) → tuple revalidation
    assert loaded.generated_sections[0].citations[0].text_span == (0, 58)


def test_restored_fact_store_behaves_identically(tmp_path: Path) -> None:
    store, facts, review, sections = _populated_state()
    save_snapshot(facts, review, sections, directory=tmp_path)
    loaded = load_snapshot(directory=tmp_path)
    assert loaded is not None

    restored = restore_fact_store(loaded.facts)

    # superseded original excluded; only the confirmed replacement is live
    for key in ("issuer_name", "issue_size_paise", "board_size"):
        assert restored.confirmed_by_key(key) == store.confirmed_by_key(key)
    live = restored.confirmed_by_key("issue_size_paise")
    assert len(live) == 1
    assert live[0].value == 120_000_000_00
    assert live[0].provenance.supersedes is not None

    # unconfirmed fact stays unconfirmed (still never feeds generation)
    assert restored.confirmed_by_key("board_size") == []

    # fact_id / confirmed / provenance all preserved verbatim
    for fact in loaded.facts:
        assert restored.get(fact.fact_id) == fact

    assert restored.all_confirmed() == store.all_confirmed()


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_snapshot(directory=tmp_path) is None
    assert load_snapshot(directory=tmp_path / "never_created") is None


def test_corrupt_file_returns_none_without_raising(tmp_path: Path) -> None:
    target = tmp_path / SNAPSHOT_FILENAME
    target.write_text("{{{ not json at all", encoding="utf-8")
    assert load_snapshot(directory=tmp_path) is None

    # valid JSON but the wrong shape is equally corrupt
    target.write_text('{"facts": "nope"}', encoding="utf-8")
    assert load_snapshot(directory=tmp_path) is None


def test_write_is_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    _, facts, review, sections = _populated_state()
    save_snapshot(facts, review, sections, directory=tmp_path)
    # overwrite an existing snapshot too (os.replace over the old file)
    save_snapshot(facts, review, sections, directory=tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == [SNAPSHOT_FILENAME]


def test_clear_snapshot_idempotent(tmp_path: Path) -> None:
    clear_snapshot(directory=tmp_path)  # nothing there — must not raise
    _, facts, review, sections = _populated_state()
    save_snapshot(facts, review, sections, directory=tmp_path)
    clear_snapshot(directory=tmp_path)
    assert not (tmp_path / SNAPSHOT_FILENAME).exists()
    clear_snapshot(directory=tmp_path)  # second clear is a no-op
    assert load_snapshot(directory=tmp_path) is None


def test_empty_session_round_trips(tmp_path: Path) -> None:
    save_snapshot([], ReviewState(), [], directory=tmp_path)
    loaded = load_snapshot(directory=tmp_path)
    assert isinstance(loaded, SessionSnapshot)
    assert loaded.facts == []
    assert loaded.review_state == ReviewState()
    assert loaded.generated_sections == []
    assert restore_fact_store(loaded.facts).all_confirmed() == []
