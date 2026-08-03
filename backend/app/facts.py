"""Fact store: immutable facts with provenance, confidence, confirmation, and role.

Rules (see CLAUDE.md):
- An unconfirmed fact never feeds generation.
- Facts are immutable once confirmed; corrections create a new fact version
  with provenance pointing at the one it supersedes.
- All monetary values are INR integers (paise) — never floats.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.schema.models import Role


class SourceKind(StrEnum):
    WIZARD = "wizard"          # typed directly by the promoter
    DOCUMENT = "document"      # extracted from an upload
    LOOKUP = "lookup"          # litigation connector etc.
    ROLE_UPLOAD = "role_upload"  # auditor/banker-supplied content (ingested, never generated)


class Provenance(BaseModel):
    kind: SourceKind
    # wizard question id / "filename.pdf p.12" / connector name — short by
    # construction, but bounded so a client can't stuff an arbitrarily large
    # string into a field that's meant to be a short label.
    detail: str = Field(max_length=500)
    # highlighted source text shown at confirmation — can be a real sentence
    # or two from a filed document, so the cap is generous, not tight.
    snippet: str | None = Field(default=None, max_length=5000)
    supersedes: str | None = None    # fact_id of the version this one corrects
    # Links back to the archived original in app.intake.vault (see
    # ArchivedDocumentMeta) so a reviewer can open the real source document
    # instead of trusting detail/snippet as bare text — the inline
    # document-viewer feature. None for anything not sourced from an upload
    # (wizard answers, lookups) or when archiving failed (best-effort, see
    # app.main's uploads_extract).
    document_id: str | None = Field(default=None, max_length=64)
    # 1-indexed page within that document the snippet was found on. None
    # alongside document_id, or for a single-page source (e.g. a standalone
    # image upload) where "page" isn't a separately meaningful concept.
    page: int | None = Field(default=None, ge=1)
    # The archived document's own filename — kept alongside document_id
    # (rather than parsed back out of detail's "filename p.N" convention) so
    # the frontend can pick a PDF/image/text rendering path without string
    # surgery on a field meant for human display.
    source_file: str | None = Field(default=None, max_length=255)


class Fact(BaseModel, frozen=True):
    """One confirmed-or-pending value keyed into the fact ontology."""

    fact_id: str = Field(default_factory=lambda: str(uuid4()))
    # ontology key, e.g. "issue_size_paise", "share_allotments[]" — always a
    # short identifier from the checklist schema, never free text.
    key: str = Field(max_length=200)
    value: Any
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)  # 1.0 for wizard answers
    confirmed: bool = False
    supplied_by: Role
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Who actually performed THIS correction — None for an original (never-
    # corrected) fact. Deliberately separate from supplied_by, which keeps
    # meaning "whose role vouches for this value" (see app.main's
    # _require_own_fact) and is preserved unchanged across a correction.
    # corrected_by_role is the feedback-loop signal app.extraction_reliability
    # reads: a banker correcting a promoter-supplied extraction is exactly
    # the due-diligence catch that signal exists to surface.
    corrected_by_role: Role | None = None


class FactStore:
    """In-memory store; append-only. Persistence is a production concern (docs, not code)."""

    def __init__(self) -> None:
        self._facts: dict[str, Fact] = {}

    def add(self, fact: Fact) -> Fact:
        self._facts[fact.fact_id] = fact
        return fact

    def confirm(self, fact_id: str) -> Fact:
        """Confirmation freezes the fact. Returns the confirmed copy."""
        fact = self._facts[fact_id]
        confirmed = fact.model_copy(update={"confirmed": True})
        self._facts[fact_id] = confirmed
        return confirmed

    def correct(
        self,
        fact_id: str,
        new_value: Any,
        provenance: Provenance,
        *,
        corrected_by_role: Role | None = None,
    ) -> Fact:
        """Corrections never mutate: a new version supersedes the old one."""
        old = self._facts[fact_id]
        replacement = Fact(
            key=old.key,
            value=new_value,
            provenance=provenance.model_copy(update={"supersedes": fact_id}),
            supplied_by=old.supplied_by,
            corrected_by_role=corrected_by_role,
        )
        return self.add(replacement)

    def get(self, fact_id: str) -> Fact:
        return self._facts[fact_id]

    def confirmed_by_key(self, key: str) -> list[Fact]:
        """Only confirmed, non-superseded facts — the generator's sole input."""
        superseded = {
            f.provenance.supersedes for f in self._facts.values() if f.provenance.supersedes
        }
        return [
            f
            for f in self._facts.values()
            if f.key == key and f.confirmed and f.fact_id not in superseded
        ]

    def all_confirmed(self) -> list[Fact]:
        superseded = {
            f.provenance.supersedes for f in self._facts.values() if f.provenance.supersedes
        }
        return [f for f in self._facts.values() if f.confirmed and f.fact_id not in superseded]

    def all_facts(self) -> list[Fact]:
        """Every fact ever stored — confirmed, unconfirmed, and superseded alike.

        Exists for the exchange-ready bundle's audit trail (insertion order).
        Generation must keep using :meth:`confirmed_by_key` / :meth:`all_confirmed`;
        an unconfirmed fact never feeds generation.
        """
        return list(self._facts.values())
