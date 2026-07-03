"""Banker certification workflow: section states, edit audit trail, cert lock.

The certification lock is a feature demanded by the problem statement: the
exchange-ready package cannot be exported until every blocker-severity
section is certified.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schema.models import Checklist, Severity


class SectionState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    CERTIFIED = "certified"


class BankerEdit(BaseModel):
    entry_id: str
    editor: str                  # banker identity (demo-grade; real auth is a prod concern)
    before: str
    after: str
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReviewState(BaseModel):
    states: dict[str, SectionState] = {}          # entry_id → state
    audit_trail: list[BankerEdit] = []

    def advance(self, entry_id: str, to: SectionState) -> None:
        order = [SectionState.DRAFT, SectionState.REVIEWED, SectionState.CERTIFIED]
        current = self.states.get(entry_id, SectionState.DRAFT)
        if order.index(to) != order.index(current) + 1:
            raise ValueError(f"{entry_id}: cannot move {current} → {to}; states advance one step")
        self.states[entry_id] = to

    def record_edit(self, edit: BankerEdit) -> None:
        self.audit_trail.append(edit)
        # any banker edit drops the section back to reviewed-pending
        self.states[edit.entry_id] = SectionState.DRAFT


def export_allowed(checklist: Checklist, review: ReviewState) -> tuple[bool, list[str]]:
    """The certification lock. Returns (allowed, uncertified blocker entry ids)."""
    blocking = [
        e.id
        for e in checklist.entries
        if e.severity == Severity.BLOCKER
        and review.states.get(e.id, SectionState.DRAFT) != SectionState.CERTIFIED
    ]
    return (not blocking, blocking)
