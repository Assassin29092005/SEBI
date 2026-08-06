"""Clause-text retrieval: map ``clause_ref`` → actual ICDR regulation passage.

The checklist schema entries carry a ``clause_ref`` string (e.g. ``"ICDR
Sch. VI Part A, para (9)"``), but this is just a citation label — the
actual regulation text lives in ``data/regulation/``.  This module bridges
that gap: given a ``clause_ref``, return the relevant passage from the
regulation source files.

The mapping is built once at process start by scanning the regulation text
files for the headings our checklist actually cites:
- ``Sch. VI Part A, para (N)`` → Schedule VI Part A paragraph N
- ``Reg. NNN`` → Chapter IX regulation NNN

Returning the *wrong* passage next to a citation would be worse than
returning none — it would quietly undermine the "every sentence is
traceable" guarantee this feature exists to strengthen. Both indexes are
therefore built to fail closed: an unmatched clause_ref yields ``None``
and the UI simply doesn't show a passage.
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.config import settings

_REG_DIR = settings.data_dir / "regulation"

_NUMBERED_LINE = re.compile(r"^\((\d+)\)\s*(.*)$")
_REG_LINE = re.compile(r"^(\d{3})\.\s*(.*)$")
_PAGE_NUMBER_LINE = re.compile(r"^\d{2,4}\s*$")

# Schedule VI Part A starts here and ends at the next Part heading. Without
# these bounds the scan also picks up the Schedule V due-diligence
# certificate's own (1)…(14) list that precedes it in the same file.
_PART_A_START = re.compile(r"^Part\s+A\b", re.IGNORECASE)
_PART_END = re.compile(r"^(Part\s+[B-Z]\b|SCHEDULE\s+[IVX]+\b)", re.IGNORECASE)

# Footnote markers the PDF text layer leaves inline: "602[*]", "689[fast
# track public issue ]", "[***]". Stripped before a heading is matched.
_FOOTNOTE_MARKER = re.compile(r"\d{3}\[|\[\*+\]|[\[\]]")

# Schedule VI Part A's eighteen top-level paragraph titles, as they appear
# in the pinned source text (ICDR consolidated to 2025-03-08 +
# 2026 amendment; see data/regulation/MANIFEST.md).
#
# These are hand-verified data rather than an inferred structure, and that
# is deliberate. Part A's top-level paragraphs are numbered (1)…(18), but
# so is every nested list inside them — para (5) Risk factors alone
# contains its own (1)…(31), and para (15) Offering Information contains
# (1)…(28). In the PDF's extracted text layer the nesting carries no
# indentation, so "(16)" as a top-level heading and "(16)" as an item
# inside para (15) are character-for-character indistinguishable in shape.
# Every purely structural heuristic tried here (ascending sequence,
# short-title-with-colon, and the two combined) mis-segments at least one
# real paragraph. Since the regulation version is pinned anyway and the
# schema is human-reviewed legal-adjacent content by policy, pinning the
# titles too is the honest fix — and it fails closed when SEBI amends the
# text, because an unmatched title simply drops out of the index.
_PART_A_TITLES: dict[int, str] = {
    1: "Cover pages",
    2: "Table of Contents",
    3: "Definitions and abbreviations",
    4: "Offer Document summary",
    5: "Risk factors",
    6: "Introduction",
    7: "General information",
    8: "Capital structure",
    9: "Particulars of the issue",
    10: "About the Issuer",
    11: "Financial Statements",
    12: "Legal and Other Information",
    13: "Information with respect to group companies",
    14: "Other Regulatory and Statutory Disclosures",
    15: "Offering Information",
    16: "Any other material disclosures",
    17: "In case of a fast track public issue",
    18: "Other Information",
}

_MAX_DISPLAY_LINES = 40


def _normalise(text: str) -> str:
    """Strip footnote markers and collapse whitespace for heading matching."""
    return " ".join(_FOOTNOTE_MARKER.sub(" ", text).split()).casefold()


def _load_file(filename: str) -> str:
    """Load a regulation text file, returning empty string if missing."""
    path = _REG_DIR / filename
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return ""


def _build_schedule_vi_index(text: str) -> dict[str, str]:
    """Index Schedule VI **Part A** top-level paragraphs: ``"N"`` → passage.

    A line opens paragraph N only when it is numbered ``(N)`` *and* its
    text begins with N's pinned title (see ``_PART_A_TITLES``) *and* N is
    the next paragraph expected. All three conditions are needed: number
    and title alone would match a nested item that happens to repeat a
    heading, and sequence alone can't tell nesting from structure at all.
    """
    index: dict[str, str] = {}
    if not text:
        return index

    in_part_a = False
    expected_next = 1
    current_para: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_para is not None:
            index[current_para] = "\n".join(current_lines).strip()

    for raw_line in text.split("\n"):
        line = raw_line.strip()

        if not in_part_a:
            in_part_a = bool(_PART_A_START.match(line))
            continue
        if _PART_END.match(line):
            break

        match = _NUMBERED_LINE.match(line)
        if (
            match
            and int(match.group(1)) == expected_next
            and _normalise(match.group(2)).startswith(
                _normalise(_PART_A_TITLES.get(expected_next, "\0"))
            )
        ):
            _flush()
            current_para = match.group(1)
            current_lines = [line]
            expected_next += 1
        elif current_para is not None:
            if _PAGE_NUMBER_LINE.match(line):
                continue  # PDF page-number artefact
            current_lines.append(line)

    _flush()
    return index


def _build_chapter_ix_index(text: str) -> dict[str, str]:
    """Index Chapter IX regulations: key = "NNN" → regulation text."""
    index: dict[str, str] = {}
    if not text:
        return index

    lines = text.split("\n")
    current_reg: str | None = None
    current_lines: list[str] = []

    for line in lines:
        # Check if this line starts a new regulation (e.g. "228." or "228. (1)")
        m = _REG_LINE.match(line.strip())
        if m:
            if current_reg is not None:
                index[current_reg] = "\n".join(current_lines).strip()
            current_reg = m.group(1)
            current_lines = [line.strip()]
        elif current_reg is not None:
            stripped = line.strip()
            if _PAGE_NUMBER_LINE.match(stripped):
                continue  # skip page numbers
            current_lines.append(stripped)

    if current_reg is not None:
        index[current_reg] = "\n".join(current_lines).strip()

    return index


@lru_cache(maxsize=1)
def _build_full_index() -> tuple[dict[str, str], dict[str, str]]:
    """Build both indices, cached for the process lifetime."""
    sch_vi_text = _load_file("schedule_vi_disclosures.txt")
    ch_ix_text = _load_file("chapter_ix_sme_ipo.txt")
    return _build_schedule_vi_index(sch_vi_text), _build_chapter_ix_index(ch_ix_text)


def get_clause_text(clause_ref: str) -> str | None:
    """Look up the regulation text for a ``clause_ref`` string.

    Tries to extract paragraph numbers from the clause_ref and return the
    matching passage.  Returns ``None`` if no match is found.

    Examples:
        >>> get_clause_text("ICDR Sch. VI Part A, para (9)")
        "(9) Objects of the Issue..."

        >>> get_clause_text("ICDR Reg. 230(2)")
        "230. (2) The amount for general corporate purposes..."
    """
    sch_vi_index, ch_ix_index = _build_full_index()

    # Try Schedule VI paragraph match: "para (N)" or "para (N)(X)"
    para_match = re.search(r"para\s*\((\d+)\)", clause_ref)
    if para_match:
        passage = sch_vi_index.get(para_match.group(1))
        if passage:
            return _truncate(passage)

    # Try Chapter IX regulation match: "Reg. NNN"
    reg_match = re.search(r"Reg\.\s*(\d{3})", clause_ref)
    if reg_match:
        passage = ch_ix_index.get(reg_match.group(1))
        if passage:
            return _truncate(passage)

    return None


def _truncate(passage: str) -> str:
    """Cap a passage at a readable length for inline display."""
    lines = passage.split("\n")
    if len(lines) > _MAX_DISPLAY_LINES:
        return "\n".join(lines[:_MAX_DISPLAY_LINES]) + "\n[...truncated]"
    return passage


def list_available_clauses() -> list[str]:
    """Return all clause keys that have indexed text available."""
    sch_vi_index, ch_ix_index = _build_full_index()
    clauses: list[str] = []
    for para_num in sorted(sch_vi_index.keys(), key=int):
        clauses.append(f"Sch. VI para ({para_num})")
    for reg_num in sorted(ch_ix_index.keys(), key=int):
        clauses.append(f"Reg. {reg_num}")
    return clauses
