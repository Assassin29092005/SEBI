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
    detail: str                      # wizard question id / "filename.pdf p.12" / connector name
    snippet: str | None = None       # highlighted source text shown at confirmation
    supersedes: str | None = None    # fact_id of the version this one corrects


class Fact(BaseModel, frozen=True):
    """One confirmed-or-pending value keyed into the fact ontology."""

    fact_id: str = Field(default_factory=lambda: str(uuid4()))
    key: str                         # ontology key, e.g. "issue_size_paise", "share_allotments[]"
    value: Any
    provenance: Provenance
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)  # 1.0 for wizard answers
    confirmed: bool = False
    supplied_by: Role
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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

    def correct(self, fact_id: str, new_value: Any, provenance: Provenance) -> Fact:
        """Corrections never mutate: a new version supersedes the old one."""
        old = self._facts[fact_id]
        replacement = Fact(
            key=old.key,
            value=new_value,
            provenance=provenance.model_copy(update={"supersedes": fact_id}),
            supplied_by=old.supplied_by,
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
