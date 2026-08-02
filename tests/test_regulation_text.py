"""Clause-text retrieval (app.regulation_text): resolving a checklist entry's
clause_ref to the real, verbatim ICDR passage it cites.

The headline regression guard here is test_every_real_schema_clause_ref_resolves_
something — it pins that every clause_ref actually shipped in the checklist YAML
resolves to at least one real passage, exactly mirroring the manual verification
this module was built against (see its module docstring).
"""

from __future__ import annotations

import pytest
from app.regulation_text import (
    _SCHEDULE_VI_PART_A_ANCHORS,
    RegulationTextError,
    _verify_anchor,
    find_clause_text,
)
from app.schema.loader import load_checklist

# --------------------------------------------------------------------------
# Chapter IX: automated regulation-number resolution
# --------------------------------------------------------------------------


def test_single_regulation_resolves_with_correct_heading() -> None:
    result = find_clause_text("ICDR Reg. 228")
    assert len(result.passages) == 1
    passage = result.passages[0]
    assert passage.locator == "ICDR Reg. 228"
    assert passage.heading.startswith("228.")
    assert "not be eligible" in passage.text
    assert result.unresolved == []


def test_regulation_with_subclause_suffix_still_resolves() -> None:
    result = find_clause_text("ICDR Reg. 246(3)")
    assert len(result.passages) == 1
    assert result.passages[0].locator == "ICDR Reg. 246"


def test_regulation_range_expands_to_every_regulation_in_between() -> None:
    result = find_clause_text("lock-in per ICDR Reg. 238–242 (Ch. IX)")
    locators = [p.locator for p in result.passages]
    assert locators == [f"ICDR Reg. {n}" for n in range(238, 243)]


def test_duplicate_regulation_mentions_are_deduplicated() -> None:
    result = find_clause_text("OFS cap per Reg. 230(1)(f)–(g); firm financing per Reg. 230(1)(e)")
    assert [p.locator for p in result.passages] == ["ICDR Reg. 230"]
    assert result.unresolved == []  # both fragments resolved via the one Reg. 230 match


def test_footnote_definitions_do_not_leak_into_the_returned_passage() -> None:
    """Reg. 228's real footnote definitions ("383 Substituted by the
    Securities and Exchange Board of India...") sit physically between its
    operative text and Reg. 229 in the source file — must be stripped."""
    result = find_clause_text("ICDR Reg. 228")
    text = result.passages[0].text
    assert "Substituted by the Securities and Exchange Board" not in text
    assert "Inserted by the Securities and Exchange Board" not in text


def test_bare_page_number_lines_are_stripped() -> None:
    result = find_clause_text("ICDR Reg. 230")
    lines = result.passages[0].text.splitlines()
    assert not any(line.strip().isdigit() for line in lines)


def test_unknown_regulation_number_produces_no_passage() -> None:
    result = find_clause_text("ICDR Reg. 999")
    assert result.passages == []


# --------------------------------------------------------------------------
# Schedule VI Part A: hand-verified anchor resolution
# --------------------------------------------------------------------------


def test_part_a_paragraph_resolves_with_correct_heading() -> None:
    result = find_clause_text("ICDR Sch. VI Part A, para (13)")
    assert len(result.passages) == 1
    passage = result.passages[0]
    assert passage.locator == "ICDR Sch. VI Part A, para (13)"
    assert "group companies" in passage.heading.lower()


def test_instructions_block_resolves() -> None:
    result = find_clause_text("ICDR Sch. VI Part A, Instructions (a), (f)–(g)")
    assert len(result.passages) == 1
    assert result.passages[0].locator == "ICDR Sch. VI Part A, Instructions"
    assert "(a)" in result.passages[0].text
    assert "(g)" in result.passages[0].text


def test_singular_instruction_also_resolves() -> None:
    result = find_clause_text("ICDR Sch. VI Part A, Instruction (e)")
    assert len(result.passages) == 1
    assert result.passages[0].locator == "ICDR Sch. VI Part A, Instructions"


def test_para_reference_not_physically_near_part_a_still_resolves() -> None:
    """A ref that spells out "Part A" once but cites a second para later in
    the string, well past any reasonable proximity window, must still
    resolve — every para (N) in this schema means Part A, full stop."""
    result = find_clause_text(
        "ICDR Sch. VI Part A, para (11)(A)(g); summary in para (6)(D) per ICDR (Amendment) Regulations, 2026"
    )
    locators = {p.locator for p in result.passages}
    assert locators == {"ICDR Sch. VI Part A, para (11)", "ICDR Sch. VI Part A, para (6)"}
    assert result.unresolved == []


def test_unknown_part_a_paragraph_number_produces_no_passage() -> None:
    result = find_clause_text("ICDR Sch. VI Part A, para (99)")
    assert result.passages == []


# --------------------------------------------------------------------------
# Anchor integrity: a stale/re-pinned source file must fail loudly
# --------------------------------------------------------------------------


def test_verify_anchor_raises_on_mismatched_heading() -> None:
    start, _end, _heading = _SCHEDULE_VI_PART_A_ANCHORS["13"]
    with pytest.raises(RegulationTextError, match="no longer matches"):
        _verify_anchor("13", start, "this heading text does not exist in the file")


def test_every_anchor_verifies_clean_against_the_real_source_file() -> None:
    """The inverse of the above: every hand-verified anchor this module ships
    with must actually match the real, checked-in source file right now."""
    for key, (start, _end, heading) in _SCHEDULE_VI_PART_A_ANCHORS.items():
        _verify_anchor(key, start, heading)  # raises on failure


# --------------------------------------------------------------------------
# Unresolved: never invented, always reported
# --------------------------------------------------------------------------


def test_non_citable_ref_resolves_to_nothing_and_nothing_unresolved() -> None:
    result = find_clause_text("plain descriptive text, no legal citation whatsoever")
    assert result.passages == []
    assert result.unresolved == []


def test_schedule_vi_part_e_annexure_is_reported_as_unresolved_not_guessed() -> None:
    result = find_clause_text(
        "Sch. VI Part E Annexure I items (1)–(2) (summary of business and industry)"
    )
    assert result.passages == []
    assert len(result.unresolved) == 1
    assert "Annexure" in result.unresolved[0]


def test_form_reference_is_reported_as_unresolved() -> None:
    result = find_clause_text("ICDR Reg. 246(3) + Form A / Form G of Schedule V; site visit report annexed")
    # Reg. 246 resolves for real; the Form A/G fragment does not and must say so.
    assert any(p.locator == "ICDR Reg. 246" for p in result.passages)
    assert any("Form A" in u for u in result.unresolved)


# --------------------------------------------------------------------------
# Truncation
# --------------------------------------------------------------------------


def test_long_passage_is_truncated_with_a_flag() -> None:
    result = find_clause_text("ICDR Sch. VI Part A, para (11)")  # Financial Statements — the biggest
    passage = result.passages[0]
    assert passage.truncated is True
    assert passage.text.endswith("…")
    assert len(passage.text) <= 4001  # cap + ellipsis


def test_short_passage_is_not_truncated() -> None:
    result = find_clause_text("ICDR Sch. VI Part A, para (3)")  # Definitions — short
    assert result.passages[0].truncated is False
    assert not result.passages[0].text.endswith("…")


# --------------------------------------------------------------------------
# The headline regression guard: every real shipped clause_ref resolves
# --------------------------------------------------------------------------


def test_every_real_schema_clause_ref_resolves_something() -> None:
    """Pins the manual verification this module was built against: every
    clause_ref actually shipped in the checklist YAML must resolve to at
    least one real passage. A future schema edit that adds an entry citing
    something this module can't resolve should fail CI, not ship silently
    broken."""
    checklist = load_checklist()
    unresolved_entries = []
    for entry in checklist.entries:
        result = find_clause_text(entry.clause_ref)
        if not result.passages:
            unresolved_entries.append((entry.id, entry.clause_ref))
    assert unresolved_entries == []
