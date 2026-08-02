"""Clause-text retrieval: show the actual pinned ICDR passage a `clause_ref`
citation points to, not just the citation string.

The core trust guarantee this app already makes is "every sentence is
traceable" (see CLAUDE.md's Guiding Principles) — a generated sentence links
back to the fact that supports it. This closes the other half: a checklist
requirement's `clause_ref` (e.g. ``"ICDR Sch. VI Part A, para (9)(A), (E)-(I);
OFS cap per Reg. 230(1)(f)-(g)"``) is itself a citation, and until now it was
just a string a promoter had to trust or go look up themselves. This module
resolves it to the real regulatory text, sourced verbatim from
``data/regulation/`` — never LLM-paraphrased, never reworded. A reference
that can't be confidently resolved is reported as unresolved, never guessed
at with a plausible-looking wrong passage — showing the wrong clause next to
a citation would actively damage the trust guarantee this feature exists to
strengthen, which is worse than showing nothing.

Two source files, two different reliability postures:

- ``chapter_ix_sme_ipo.txt`` (flat "Reg. NNN" numbering, globally unique and
  sequential across the whole Act): resolved by an AUTOMATED regex scan.
  Confirmed safe for the regulation numbers this app actually cites (228,
  229, 230, 236, 238-242, 246, 250) — each resolves to exactly one
  unambiguous match. A small number of OTHER regulation numbers in this file
  have duplicate/non-monotonic matches from the source PDF extraction
  (verified by hand: 259/260, 274) — none of them are cited by this app's
  checklist, so this is a documented gap, not a silent one; a lookup for one
  of those returns its first match, unverified.

- ``schedule_vi_disclosures.txt`` (Schedule VI Part A's numbered paragraphs
  reuse small numbers at multiple nesting depths — e.g. a Risk Factors list
  item and the top-level "(6) Introduction:" heading are BOTH `"(6)"` at the
  start of a line, structurally indistinguishable by regex alone): resolved
  by a HAND-VERIFIED anchor table (`_SCHEDULE_VI_PART_A_ANCHORS`), the same
  "every entry is human-reviewed before it ships" discipline this project
  already applies to the checklist schema itself. Each anchor is checked
  against the live file at first use (`_verify_anchor`) — a re-extraction of
  the source PDF that shifts line numbers fails loudly (`RegulationTextError`)
  rather than silently serving the wrong paragraph.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

from pydantic import BaseModel

from app.config import settings

# Each returned passage is capped — some Part A paragraphs run 500+ lines
# (para (11), Financial Statements, is the biggest) and a UI panel showing
# the full text isn't the point; enough to show the actual clause language a
# citation rests on is. `truncated` tells the caller there's more.
_MAX_PASSAGE_CHARS = 4000


class RegulationTextError(Exception):
    """A hand-verified anchor no longer matches the source file — the file
    was re-extracted/re-pinned and this module's line numbers are stale."""


class ClausePassage(BaseModel):
    locator: str  # human label, e.g. "ICDR Reg. 230", "ICDR Sch. VI Part A, para (9)"
    heading: str  # the passage's own heading line, e.g. "230. (1) An issuer..."
    text: str
    source_file: str
    truncated: bool


class ClauseTextResult(BaseModel):
    passages: list[ClausePassage]
    # Fragments of the clause_ref this module could not confidently resolve
    # (e.g. "Sch. VI Part E Annexure I", "Form A / Form G of Schedule V") —
    # reported, never silently dropped or guessed at.
    unresolved: list[str]


# --------------------------------------------------------------------------
# Source file access
# --------------------------------------------------------------------------


def _regulation_dir() -> Path:
    return settings.data_dir / "regulation"


@functools.lru_cache(maxsize=2)
def _read_lines(filename: str) -> tuple[str, ...]:
    path = _regulation_dir() / filename
    with path.open(encoding="utf-8") as fh:
        return tuple(line.rstrip("\n") for line in fh)


_CHAPTER_IX_FILE = "chapter_ix_sme_ipo.txt"
_SCHEDULE_VI_FILE = "schedule_vi_disclosures.txt"

# --------------------------------------------------------------------------
# Passage cleanup: strip bare page-number lines and footnote-definition
# blocks that the PDF-to-text extraction interleaves into the body text.
# --------------------------------------------------------------------------

_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")
# Footnote definitions in both files read like "383 Substituted by the
# Securities and Exchange Board of India ... w.e.f. 08.03.2025 for the word
# "registering"." — a bare number, a space, then one of a handful of verbs
# legislative drafting always uses for amendment footnotes. Continuation
# lines (the same footnote wrapping past one line) don't repeat the number,
# so once inside a footnote block this keeps skipping until a blank line.
_FOOTNOTE_START_RE = re.compile(
    r"^\d{2,4}\s+(Substituted|Inserted|Omitted|Renumbered|Deleted|Numbers?|Words?)\b"
)


def _clean_passage_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    in_footnote = False
    for line in lines:
        stripped = line.strip()
        if in_footnote:
            if not stripped:
                in_footnote = False
            continue
        if _PAGE_NUMBER_RE.match(line):
            continue
        if _FOOTNOTE_START_RE.match(line):
            in_footnote = True
            continue
        cleaned.append(line)
    return cleaned


def _make_passage(locator: str, heading: str, lines: list[str], source_file: str) -> ClausePassage:
    cleaned = _clean_passage_lines(lines)
    text = "\n".join(cleaned).strip()
    truncated = len(text) > _MAX_PASSAGE_CHARS
    if truncated:
        text = text[:_MAX_PASSAGE_CHARS].rstrip() + "…"
    return ClausePassage(
        locator=locator, heading=heading, text=text, source_file=source_file, truncated=truncated
    )


# --------------------------------------------------------------------------
# Chapter IX: automated, flat "NNN. " numbering
# --------------------------------------------------------------------------

_CHAPTER_IX_HEADING_RE = re.compile(r"^(\d{3})\.\s")


@functools.lru_cache(maxsize=1)
def _chapter_ix_starts() -> dict[int, int]:
    """Regulation number -> 0-indexed line where it starts.

    First match wins for a number that appears more than once (a handful of
    regulation numbers unrelated to anything this app cites have duplicate
    matches from the source extraction — see the module docstring).
    """
    starts: dict[int, int] = {}
    for i, line in enumerate(_read_lines(_CHAPTER_IX_FILE)):
        match = _CHAPTER_IX_HEADING_RE.match(line)
        if match:
            number = int(match.group(1))
            starts.setdefault(number, i)
    return starts


def _chapter_ix_passage(regulation_number: int) -> ClausePassage | None:
    starts = _chapter_ix_starts()
    start = starts.get(regulation_number)
    if start is None:
        return None
    lines = _read_lines(_CHAPTER_IX_FILE)
    sorted_starts = sorted(starts.values())
    end = next((s for s in sorted_starts if s > start), len(lines))
    return _make_passage(
        locator=f"ICDR Reg. {regulation_number}",
        heading=lines[start].strip(),
        lines=list(lines[start:end]),
        source_file=_CHAPTER_IX_FILE,
    )


# --------------------------------------------------------------------------
# Schedule VI Part A: hand-verified anchors (0-indexed [start, end) lines)
# --------------------------------------------------------------------------
# Verified by hand against schedule_vi_disclosures.txt as checked in (4332
# lines; Part A spans line 31 "Part A - Disclosures..." through line 3368,
# immediately before "Part B" at line 3369). Every top-level Part A
# paragraph is a short, standalone "(N) Title:" line — distinguishable from
# nested sub-list items reusing the same small numbers (e.g. a Risk Factors
# bullet "(6) Lack of significant experience..." is a full sentence with no
# trailing colon, not a heading) only by manual inspection, which is what
# this table records. Paragraph numbers not cited by the checklist schema
# (2, 4, 16, 17...) are intentionally not included — no need to carry an
# anchor this app never resolves.
#
# (start_line, end_line, expected heading substring for _verify_anchor)
_SCHEDULE_VI_PART_A_ANCHORS: dict[str, tuple[int, int, str]] = {
    "instructions": (46, 63, "Instructions:"),
    "1": (89, 192, "(1) Cover pages:"),
    "3": (195, 204, "(3) Definitions and abbreviations:"),
    "5": (303, 438, "(5) Risk factors:"),
    "6": (438, 442, "(6) Introduction:"),
    "7": (442, 536, "(7) General information:"),
    "8": (536, 777, "(8) Capital structure:"),
    "9": (777, 1365, "(9) Particulars of the issue:"),
    "10": (1365, 1777, "(10) About the Issuer:"),
    "11": (1777, 2583, "(11) Financial Statements:"),
    "12": (2583, 2682, "(12) Legal and Other Information:"),
    "13": (2682, 2765, "(13) Information with respect to group companies"),
    "14": (2765, 2962, "(14) Other Regulatory and Statutory Disclosures:"),
    "15": (2962, 3330, "(15) Offering Information:"),
    "18": (3330, 3368, "(18) Other Information:"),
}


def _verify_anchor(key: str, start: int, expected_heading: str) -> None:
    lines = _read_lines(_SCHEDULE_VI_FILE)
    if start >= len(lines) or expected_heading not in lines[start]:
        actual = lines[start].strip() if start < len(lines) else "<past end of file>"
        raise RegulationTextError(
            f"Schedule VI Part A anchor {key!r} no longer matches the source file: "
            f"expected a line containing {expected_heading!r} at line {start + 1}, "
            f"found {actual!r}. The source extraction was likely re-pinned — "
            "re-verify and update _SCHEDULE_VI_PART_A_ANCHORS in app/regulation_text.py."
        )


def _schedule_vi_part_a_passage(key: str) -> ClausePassage | None:
    anchor = _SCHEDULE_VI_PART_A_ANCHORS.get(key)
    if anchor is None:
        return None
    start, end, expected_heading = anchor
    _verify_anchor(key, start, expected_heading)
    lines = _read_lines(_SCHEDULE_VI_FILE)
    locator = (
        "ICDR Sch. VI Part A, Instructions"
        if key == "instructions"
        else f"ICDR Sch. VI Part A, para ({key})"
    )
    return _make_passage(
        locator=locator,
        heading=lines[start].strip(),
        lines=list(lines[start:end]),
        source_file=_SCHEDULE_VI_FILE,
    )


# --------------------------------------------------------------------------
# clause_ref parsing
# --------------------------------------------------------------------------

# "Reg. 230", "Regulation 230", "Reg 230(1)(f)-(g)", with an optional range
# "238-242" / "238–242" (hyphen, en dash, or em dash) immediately following.
_REG_RE = re.compile(r"Reg(?:ulation)?\.?\s*(\d{3})(?:\s*[–—-]\s*(\d{3}))?")
# Not scoped to text physically near "Part A" in the ref string — a ref like
# "para (11)(A)(g); summary in para (6)(D) per ..." cites Part A twice but
# only spells out "Part A" once, well outside any reasonable proximity
# window to the second "para". Safe to match unscoped: every "para (N)"
# citation in this checklist schema means Sch. VI Part A — nothing in it
# ever cites a bare "para (N)" for Part B/C/D/E.
_PART_A_PARA_RE = re.compile(r"\bpara(?:graph)?\.?\s*\((\d{1,2})\)", re.IGNORECASE)
_INSTRUCTION_RE = re.compile(r"Instructions?\s*\(", re.IGNORECASE)

_MAX_REG_RANGE_SPAN = 10  # sanity cap: a malformed "228-999"-style match never expands unbounded


def find_clause_text(clause_ref: str) -> ClauseTextResult:
    """Resolve every reference inside a `clause_ref` string to real regulatory text.

    A `clause_ref` often cites more than one provision (e.g. a checklist
    entry's requirement plus a numeric cap defined elsewhere) — every
    resolvable one is returned, in the order first mentioned. Nothing in
    `clause_ref` that doesn't match a known citable pattern (a Schedule VI
    Part E annexure, a cross-reference to a different Schedule, free prose)
    is invented a passage for; it's reported back as `unresolved` instead.
    """
    passages: list[ClausePassage] = []
    seen_locators: set[str] = set()

    for match in _REG_RE.finditer(clause_ref):
        start_num = int(match.group(1))
        end_num = int(match.group(2)) if match.group(2) else start_num
        if end_num < start_num or end_num - start_num > _MAX_REG_RANGE_SPAN:
            continue
        for number in range(start_num, end_num + 1):
            passage = _chapter_ix_passage(number)
            if passage is not None and passage.locator not in seen_locators:
                passages.append(passage)
                seen_locators.add(passage.locator)

    if _INSTRUCTION_RE.search(clause_ref):
        passage = _schedule_vi_part_a_passage("instructions")
        if passage is not None and passage.locator not in seen_locators:
            passages.append(passage)
            seen_locators.add(passage.locator)

    for match in _PART_A_PARA_RE.finditer(clause_ref):
        passage = _schedule_vi_part_a_passage(match.group(1))
        if passage is not None and passage.locator not in seen_locators:
            passages.append(passage)
            seen_locators.add(passage.locator)

    unresolved = _unresolved_fragments(clause_ref)
    return ClauseTextResult(passages=passages, unresolved=unresolved)


def _fragment_resolves_something(fragment: str) -> bool:
    """Would this fragment, on its own, produce at least one passage?

    Re-runs the same three resolution patterns used in find_clause_text —
    NOT a locator-string substring check, which would wrongly flag a
    fragment like "OFS cap per Reg. 230(1)(f)-(g)" as unresolved just
    because it doesn't repeat the "ICDR" prefix that only appears once at
    the start of the whole clause_ref string, even though Reg. 230 was
    genuinely matched (via a different fragment of the same string).
    """
    if _INSTRUCTION_RE.search(fragment):
        return True
    if _PART_A_PARA_RE.search(fragment):
        return True
    known = _chapter_ix_starts()
    for match in _REG_RE.finditer(fragment):
        start_num = int(match.group(1))
        end_num = int(match.group(2)) if match.group(2) else start_num
        if start_num <= end_num and any(n in known for n in range(start_num, end_num + 1)):
            return True
    return False


def _unresolved_fragments(clause_ref: str) -> list[str]:
    """Best-effort: report semicolon/`+`-separated fragments that mention a
    citable-looking pattern (Reg./Sch./para/Instruction/Form/Annexure) but
    produced no matching passage — never a claim of completeness, just a
    pointer to what a reader should look up in the source PDFs themselves."""
    citable_hint = re.compile(
        r"\b(Reg(?:ulation)?\.?|Sch(?:edule)?\.?|para(?:graph)?\.?|Instructions?|Form|Annexure)\b",
        re.IGNORECASE,
    )
    unresolved: list[str] = []
    for fragment in re.split(r"[;+]", clause_ref):
        fragment = fragment.strip()
        if not fragment or not citable_hint.search(fragment):
            continue
        if _fragment_resolves_something(fragment):
            continue
        unresolved.append(fragment)
    return unresolved
