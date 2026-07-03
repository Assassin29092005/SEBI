"""Contradiction check: numeric/entity consistency across generated sections.

"Page 40 disagrees with page 12." Extract all numeric and entity claims from
the generated sections, then cross-check. The planted-error live demo depends
on this module.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.generate.sections import GeneratedSection


class Claim(BaseModel):
    section_entry_id: str
    kind: str                    # "number" | "entity" | "date"
    subject: str                 # what the claim is about, e.g. "issue_size"
    value: str
    text_span: tuple[int, int]


class Contradiction(BaseModel):
    subject: str
    claims: list[Claim]          # the mutually inconsistent claims


async def extract_claims(section: GeneratedSection) -> list[Claim]:
    """TODO (day 5–6): LLM-assisted claim extraction via app.llm.client, temp 0."""
    raise NotImplementedError("claim extraction: day 5–6 deliverable")


def cross_check(claims: list[Claim]) -> list[Contradiction]:
    """Group claims by subject; flag groups whose values disagree.

    TODO (day 5–6): numeric normalisation (paise, lakh/crore forms) before compare.
    """
    raise NotImplementedError("contradiction cross-check: day 5–6 deliverable")
