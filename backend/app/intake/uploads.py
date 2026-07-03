"""Document upload handling and fact extraction.

Extracted values are *proposals* until the promoter confirms them against the
highlighted source snippet — an unconfirmed fact never feeds generation.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.facts import Fact, Provenance, SourceKind
from app.schema.models import Role


class ExtractionProposal(BaseModel):
    """A candidate fact awaiting promoter confirmation."""

    fact_key: str
    value: str
    source_file: str
    page: int
    snippet: str          # highlighted source text shown to the promoter
    confidence: float


async def extract_facts(filename: str, content: bytes) -> list[ExtractionProposal]:
    """Run LLM/OCR extraction over an uploaded document.

    TODO: implement via app.llm.client — prompt returns (key, value, page,
    snippet, confidence) tuples constrained to the fact ontology keys.
    """
    raise NotImplementedError("upload extraction: day 3–4 deliverable")


def proposal_to_fact(proposal: ExtractionProposal) -> Fact:
    """Materialise a confirmed proposal as an unconfirmed Fact (confirmation is separate)."""
    return Fact(
        key=proposal.fact_key,
        value=proposal.value,
        provenance=Provenance(
            kind=SourceKind.DOCUMENT,
            detail=f"{proposal.source_file} p.{proposal.page}",
            snippet=proposal.snippet,
        ),
        confidence=proposal.confidence,
        supplied_by=Role.PROMOTER,
    )
