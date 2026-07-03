"""Document assembly via python-docx: the draft DRHP and the second deliverable.

⚠️ Verify against the March-2026 ICDR amendment whether the second deliverable
is still the "abridged prospectus" (March-2025 amendments) or the "Offer
Document Summary" (Nov-2025 consultation) — pin whichever is current and cite
it in the schema before implementing OutputTarget.ABRIDGED assembly.
"""

from __future__ import annotations

from pathlib import Path

from app.generate.sections import GeneratedSection
from app.schema.models import Checklist, OutputTarget


def assemble(
    checklist: Checklist,
    sections: list[GeneratedSection],
    target: OutputTarget,
    out_path: Path,
) -> Path:
    """Assemble the formatted document for one output target.

    TODO (day 7): python-docx layout — cover page, TOC, section ordering from
    the schema, [REQUIRES INPUT] markers visually distinct, citation footnotes.
    Lakh/crore formatting happens here (display layer) — values arrive as paise.
    """
    raise NotImplementedError("docx assembly: day 7 deliverable")
