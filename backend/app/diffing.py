"""Draft version diffing: a generic, deterministic word-level diff between
two text snapshots.

Not a database of historical section text — the app already captures two
kinds of "before" text worth comparing against a later "after":

- banker edits (``app.review.workflow.BankerEdit.before``/``after``,
  already stored in the review audit trail), and
- an iterative-examiner revision round (``app.validate.iterative_examiner``
  revises a section's text and re-examines) — the frontend holds the
  pre-revision text locally (it had to, to display it before the call) and
  can diff it against ``final_sections`` from the same response.

Rather than add a stored "version history" table for either, this module
is a stateless computation over whatever two strings the caller already
has — ``POST /api/diff`` takes ``before``/``after`` directly. Pure
``difflib`` (stdlib, no new dependency), word-level rather than
character-level so a single word being replaced doesn't render as a wall of
tiny insert/delete noise around it.
"""

from __future__ import annotations

import difflib
import re
from typing import Literal

from pydantic import BaseModel

DiffKind = Literal["equal", "insert", "delete"]

# Splits whitespace, numbers (kept atomic even with internal commas/decimal
# points — "12.50" or "1,00,000" is one semantic unit in a money-heavy
# document, not three tokens fragmented at the punctuation), word runs, and
# individual remaining punctuation characters into separate tokens. Re-
# joining every token's text reproduces the original string exactly (no
# lost/added spacing at diff boundaries); splitting punctuation off from
# words separately means trailing punctuation on one side only (e.g.
# "shares." vs "shares today.") doesn't block the matcher from still
# finding "shares" as a real, shared token.
_TOKEN_RE = re.compile(r"\s+|\d[\d,.]*\d|\d|\w+|[^\w\s]")


class DiffSegment(BaseModel):
    text: str
    kind: DiffKind


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def compute_diff(before: str, after: str) -> list[DiffSegment]:
    """Word-level diff of ``before`` -> ``after``.

    Adjacent tokens of the same kind are merged into one segment so the
    caller isn't handed one segment per word for a long unchanged run.
    """
    before_tokens = _tokenize(before)
    after_tokens = _tokenize(after)
    matcher = difflib.SequenceMatcher(a=before_tokens, b=after_tokens, autojunk=False)

    segments: list[DiffSegment] = []
    for opcode, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if opcode == "equal":
            _append(segments, "".join(before_tokens[a_start:a_end]), "equal")
        elif opcode == "delete":
            _append(segments, "".join(before_tokens[a_start:a_end]), "delete")
        elif opcode == "insert":
            _append(segments, "".join(after_tokens[b_start:b_end]), "insert")
        elif opcode == "replace":
            _append(segments, "".join(before_tokens[a_start:a_end]), "delete")
            _append(segments, "".join(after_tokens[b_start:b_end]), "insert")
    return segments


def _append(segments: list[DiffSegment], text: str, kind: DiffKind) -> None:
    if not text:
        return
    if segments and segments[-1].kind == kind:
        segments[-1] = DiffSegment(text=segments[-1].text + text, kind=kind)
    else:
        segments.append(DiffSegment(text=text, kind=kind))
