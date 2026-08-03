"""Draft version diffing (app.diffing): word-level, deterministic, stdlib-only.

Every scenario here was hand-verified against a standalone script (round-trip
reconstruction of both sides from the segments) before being written up as a
formal test.
"""

from __future__ import annotations

from itertools import pairwise

from app.diffing import compute_diff


def _reconstruct(segments, kinds: set[str]) -> str:
    return "".join(s.text for s in segments if s.kind in kinds)


def test_identical_text_is_a_single_equal_segment() -> None:
    segments = compute_diff("Identical text.", "Identical text.")
    assert len(segments) == 1
    assert segments[0].kind == "equal"
    assert segments[0].text == "Identical text."


def test_single_word_replacement_isolates_just_that_word() -> None:
    before = "Issue size: Rs 12.50 crore, comprising a fresh issue."
    after = "Issue size: Rs 14.00 crore, comprising a fresh issue."
    segments = compute_diff(before, after)
    kinds = [s.kind for s in segments]
    assert kinds == ["equal", "delete", "insert", "equal"]
    assert segments[1].text == "12.50"
    assert segments[2].text == "14.00"


def test_round_trip_reconstructs_before_and_after_exactly() -> None:
    cases = [
        ("Issue size: Rs 12.50 crore.", "Issue size: Rs 14.00 crore."),
        ("The company allotted shares in 2020.", "The company allotted 1,00,000 equity shares in 2020 to promoters."),
        ("Old sentence one. Old sentence two. Old sentence three.", "New sentence one. New sentence two."),
        ("", "Something new."),
        ("Something old.", ""),
        ("", ""),
    ]
    for before, after in cases:
        segments = compute_diff(before, after)
        assert _reconstruct(segments, {"equal", "delete"}) == before
        assert _reconstruct(segments, {"equal", "insert"}) == after


def test_a_replaced_number_is_one_atomic_delete_insert_pair() -> None:
    """A money-heavy document's numbers (with internal commas/decimal
    points) must diff as one semantic unit, not fragment at the '.' or ','
    into a misleadingly granular delete/insert around a coincidentally-
    matching separator character."""
    segments = compute_diff(
        "Issue size: Rs 12.50 crore.", "Issue size: Rs 14.00 crore."
    )
    kinds = [s.kind for s in segments]
    assert kinds == ["equal", "delete", "insert", "equal"]
    assert segments[1].text == "12.50"
    assert segments[2].text == "14.00"

    segments = compute_diff("1,00,000 equity shares.", "1,50,000 equity shares.")
    assert [s.kind for s in segments] == ["delete", "insert", "equal"]
    assert segments[0].text == "1,00,000"
    assert segments[1].text == "1,50,000"


def test_pure_insertion_has_no_delete_segments() -> None:
    segments = compute_diff("The company allotted shares.", "The company allotted 1,00,000 equity shares.")
    assert all(s.kind != "delete" for s in segments)
    assert any(s.kind == "insert" for s in segments)


def test_pure_deletion_has_no_insert_segments() -> None:
    segments = compute_diff("The company allotted extra shares today.", "The company allotted shares.")
    assert all(s.kind != "insert" for s in segments)
    assert any(s.kind == "delete" for s in segments)


def test_adjacent_same_kind_segments_are_merged() -> None:
    # A wholesale rewrite alternates delete/insert/equal — no two consecutive
    # segments should ever share a kind (compute_diff merges them if they do).
    segments = compute_diff(
        "Old sentence one. Old sentence two. Old sentence three.",
        "New sentence one. New sentence two.",
    )
    for a, b in pairwise(segments):
        assert a.kind != b.kind


def test_both_empty_yields_no_segments() -> None:
    assert compute_diff("", "") == []


def test_whitespace_is_preserved_in_reconstruction() -> None:
    before = "Line one.\nLine two.\n\nLine three with  double space."
    after = "Line one.\nLine TWO.\n\nLine three with  double space."
    segments = compute_diff(before, after)
    assert _reconstruct(segments, {"equal", "delete"}) == before
    assert _reconstruct(segments, {"equal", "insert"}) == after
