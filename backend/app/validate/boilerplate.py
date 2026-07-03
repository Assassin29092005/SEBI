"""Boilerplate detector.

Flags generated text that is generic filler or too close to reference-DRHP
phrasing (reference filings are benchmarks, never templates to copy from).

Two passes:

1. **Generic-filler phrase scan** — a fixed list of vague, marketing-flavoured
   phrases that regulators routinely flag as non-disclosure. Whole-word,
   case-insensitive match anywhere in the section text.
2. **8-gram overlap vs. reference DRHPs** — every ``*.txt`` under
   ``data/reference_drhps/`` (recursive) is tokenised on whitespace, stripped
   of surrounding punctuation, lowercased, and turned into a set of 8-word
   tuples. Any 8-gram from the section text that appears in the reference set
   is flagged as a near-duplicate. If the directory is missing or contains no
   ``*.txt`` files the pass silently no-ops — this is the offline demo default
   (reference filings are large public PDFs kept out of the repo).

Both passes suppress flags whose span lies fully inside a
``[REQUIRES INPUT: ...]`` marker, because those markers are a feature (honest
blanks) and their content (fact keys / role names) is not disclosure prose.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

from pydantic import BaseModel

from app.generate.sections import GeneratedSection

# Root of the reference-DRHP corpus. Exposed at module scope so tests can
# monkeypatch it at a temporary directory rather than writing into the real
# ``data/`` tree. Resolves to ``<repo>/data/reference_drhps``.
REFERENCE_DRHPS_DIR: Path = Path(__file__).resolve().parents[3] / "data" / "reference_drhps"

# 15 generic-filler phrases. These are the phrases exchange reviewers
# routinely single out as "marketing copy, not disclosure". Case-insensitive,
# whole-word matching.
GENERIC_FILLER_PHRASES: tuple[str, ...] = (
    "world-class",
    "best-in-class",
    "leading player",
    "state-of-the-art",
    "poised for growth",
    "cutting-edge",
    "market leader",
    "one-stop solution",
    "customer-centric",
    "value proposition",
    "synergies",
    "unlock value",
    "seamless experience",
    "robust framework",
    "in the coming years",
)

_NGRAM_SIZE = 8

# Matches the marker produced by app.generate.sections.requires_input_marker:
# [REQUIRES INPUT: <fact_key> — <role> can provide this]. Content inside a
# marker is metadata, not disclosure prose — flags overlapping it are dropped.
_MARKER_RE = re.compile(r"\[REQUIRES INPUT:[^\]]*\]")


class BoilerplateFlag(BaseModel):
    entry_id: str
    text_span: tuple[int, int]
    reason: str  # "generic filler" | "near-duplicate of reference DRHP"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _marker_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of every [REQUIRES INPUT: ...] marker in ``text``."""
    return [(m.start(), m.end()) for m in _MARKER_RE.finditer(text)]


def _inside_any(span: tuple[int, int], regions: list[tuple[int, int]]) -> bool:
    """True iff ``span`` lies fully inside one of ``regions``."""
    start, end = span
    return any(r_start <= start and end <= r_end for r_start, r_end in regions)


def _filler_pattern(phrase: str) -> re.Pattern[str]:
    """Case-insensitive whole-word regex for a filler phrase.

    Whole-word boundaries are enforced by lookarounds against non-word
    characters. We deliberately do NOT use ``\\b`` because several phrases
    contain hyphens (``state-of-the-art``) and Python's ``\\b`` treats the
    hyphen itself as a word boundary, which would make ``\\b`` match a
    substring of ``non-state-of-the-art`` — the opposite of what we want.
    """
    escaped = re.escape(phrase)
    return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)


def _strip_punct(word: str) -> str:
    """Lowercased word with leading/trailing punctuation removed."""
    return word.strip(string.punctuation).lower()


def _tokenise_positions(text: str) -> list[tuple[str, int, int]]:
    """Return ``(token, start, end)`` for every whitespace-delimited chunk.

    ``token`` is the punctuation-stripped, lowercased form used for n-gram
    comparison. Empty tokens (pure-punctuation chunks) are dropped, but the
    spans returned are the raw whitespace-chunk offsets so we can report
    accurate character positions into the original section text.
    """
    tokens: list[tuple[str, int, int]] = []
    for match in re.finditer(r"\S+", text):
        raw = match.group(0)
        stripped = _strip_punct(raw)
        if stripped:
            tokens.append((stripped, match.start(), match.end()))
    return tokens


def _reference_ngrams(directory: Path) -> set[tuple[str, ...]]:
    """Set of 8-word tuples across every ``*.txt`` under ``directory``.

    Offline-demo default: if the directory doesn't exist or has no ``.txt``
    files, returns an empty set silently — the 8-gram pass then no-ops.
    """
    ngrams: set[tuple[str, ...]] = set()
    if not directory.exists() or not directory.is_dir():
        return ngrams
    for path in sorted(directory.rglob("*.txt")):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tokens = [t for t, _, _ in _tokenise_positions(content)]
        if len(tokens) < _NGRAM_SIZE:
            continue
        for i in range(len(tokens) - _NGRAM_SIZE + 1):
            ngrams.add(tuple(tokens[i : i + _NGRAM_SIZE]))
    return ngrams


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def detect(section: GeneratedSection) -> list[BoilerplateFlag]:
    """Flag generic-filler phrases and near-duplicates of reference DRHPs.

    Both passes skip any flag whose span lies fully inside a
    ``[REQUIRES INPUT: ...]`` marker — those markers are honest-blank
    scaffolding, not disclosure prose to be graded.
    """
    text = section.text
    marker_regions = _marker_spans(text)
    flags: list[BoilerplateFlag] = []

    # Pass 1: generic filler phrases.
    for phrase in GENERIC_FILLER_PHRASES:
        for match in _filler_pattern(phrase).finditer(text):
            span = (match.start(), match.end())
            if _inside_any(span, marker_regions):
                continue
            flags.append(
                BoilerplateFlag(
                    entry_id=section.entry_id,
                    text_span=span,
                    reason="generic filler",
                )
            )

    # Pass 2: 8-gram overlap against reference DRHPs.
    reference = _reference_ngrams(REFERENCE_DRHPS_DIR)
    if reference:
        positioned = _tokenise_positions(text)
        if len(positioned) >= _NGRAM_SIZE:
            for i in range(len(positioned) - _NGRAM_SIZE + 1):
                window = positioned[i : i + _NGRAM_SIZE]
                key = tuple(tok for tok, _, _ in window)
                if key not in reference:
                    continue
                span = (window[0][1], window[-1][2])
                if _inside_any(span, marker_regions):
                    continue
                flags.append(
                    BoilerplateFlag(
                        entry_id=section.entry_id,
                        text_span=span,
                        reason="near-duplicate of reference DRHP",
                    )
                )

    return flags
