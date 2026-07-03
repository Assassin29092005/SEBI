"""Per-section grounded generation.

Pipeline per schema entry: requirement → retrieve relevant confirmed facts →
grounded prompt → section text with citations and [REQUIRES INPUT] markers.

THE core rule: the LLM writes using only facts from the store. Anything
missing renders as ``[REQUIRES INPUT: <fact> — <who can provide it>]``.
Honest blanks beat confident hallucination — no exceptions.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.facts import FactStore
from app.schema.models import ChecklistEntry


class Citation(BaseModel):
    fact_id: str
    text_span: tuple[int, int]   # character offsets into the section text


class GeneratedSection(BaseModel):
    entry_id: str
    section: str
    text: str
    citations: list[Citation]
    missing_facts: list[str]     # fact keys rendered as [REQUIRES INPUT]


def requires_input_marker(fact_key: str, responsible_role: str) -> str:
    return f"[REQUIRES INPUT: {fact_key} — {responsible_role} can provide this]"


async def generate_section(entry: ChecklistEntry, store: FactStore) -> GeneratedSection:
    """Generate one section grounded in confirmed facts only.

    TODO (day 5–6): retrieval of entry.required_facts from the store, grounded
    prompt via app.llm.client.grounded_complete, citation span extraction, and
    [REQUIRES INPUT] rendering for any key with no confirmed fact.
    """
    raise NotImplementedError("grounded generation: day 5–6 deliverable")
