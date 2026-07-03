"""Wizard question flow — DERIVED from the checklist schema, never hand-maintained.

Every question shows *why* it is asked, with the regulation clause it maps to.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schema.models import Checklist, Role


class WizardQuestion(BaseModel):
    fact_key: str
    section: str
    prompt: str            # plain-language question text
    why_we_ask: str        # promoter-facing explanation
    clause_ref: str        # the regulation clause this maps to
    checklist_entry_id: str


def derive_questions(checklist: Checklist) -> list[WizardQuestion]:
    """Build the promoter question flow from schema entries.

    Only promoter-role, non-stub entries yield wizard questions; auditor- and
    banker-role requirements route to uploads in their respective workflows.
    """
    questions: list[WizardQuestion] = []
    for entry in checklist.entries:
        if entry.stub or entry.responsible_role != Role.PROMOTER:
            continue
        for fact_key in entry.required_facts:
            questions.append(
                WizardQuestion(
                    fact_key=fact_key,
                    section=entry.section,
                    prompt=_plain_language_prompt(fact_key),
                    why_we_ask=entry.description.strip(),
                    clause_ref=entry.clause_ref,
                    checklist_entry_id=entry.id,
                )
            )
    return questions


def _plain_language_prompt(fact_key: str) -> str:
    """TODO: per-key plain-language prompt copy (multilingual is a cut-line feature)."""
    return f"Please provide: {fact_key.replace('_', ' ').removesuffix('[]')}"
