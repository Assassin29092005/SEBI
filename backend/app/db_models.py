"""Postgres table definitions (SQLModel) for the durable subset of app state.

Only ``users``, ``facts``, ``section_states``, and ``banker_edits`` live here
— everything this migration's Phase 1 moves off the in-memory-plus-encrypted-
snapshot stack and onto Postgres. The API-facing Pydantic models
(``app.facts.Fact``, ``app.review.workflow.ReviewState``/``BankerEdit``,
``app.auth.models.User``) are deliberately kept separate and DB-free — the
repo modules (``app.facts_repo``, ``app.review.repo``, the rewritten
``app.auth.store``) are the only code that translates between the two, so
every other consumer (``generate/``, ``validate/``, ``coverage.py``,
``assemble/``, and 17 of the 21 test files) never has to know Postgres
exists.

``Fact.provenance`` is flattened into first-class columns (not stored as a
single JSON blob) specifically so ``provenance_supersedes`` is indexable —
that column is what makes ``confirmed_by_key``'s "exclude anything another
fact supersedes" filter a plain indexed query instead of a full scan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# Every timestamp in the domain (Fact.created_at, User.created_at,
# BankerEdit.at) is produced via ``datetime.now(UTC)`` — timezone-aware.
# asyncpg rejects binding a tz-aware value to a plain ``TIMESTAMP`` column
# outright, so every datetime column below is explicitly ``timezone=True``
# (maps to Postgres ``TIMESTAMPTZ``). Each field gets its own ``Column(...)``
# instance — a single Column object can't be shared across tables.


class UserRow(SQLModel, table=True):
    __tablename__ = "users"

    user_id: str = Field(primary_key=True)
    email: str = Field(unique=True, index=True)
    name: str
    role: str
    password_hash: str
    password_salt: str
    disabled: bool = Field(default=False)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))


class FactRow(SQLModel, table=True):
    __tablename__ = "facts"
    __table_args__ = (Index("ix_facts_key_confirmed", "key", "confirmed"),)

    fact_id: str = Field(primary_key=True)
    key: str = Field(index=True)
    value: Any = Field(sa_column=Column(JSONB))
    provenance_kind: str
    provenance_detail: str
    provenance_snippet: str | None = None
    provenance_supersedes: str | None = Field(
        default=None, foreign_key="facts.fact_id", index=True
    )
    provenance_document_id: str | None = None
    provenance_page: int | None = None
    provenance_source_file: str | None = None
    confidence: float
    confirmed: bool = Field(default=False, index=True)
    supplied_by: str
    corrected_by_role: str | None = None
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))


class SectionStateRow(SQLModel, table=True):
    __tablename__ = "section_states"

    entry_id: str = Field(primary_key=True)
    state: str = Field(default="draft")


class BankerEditRow(SQLModel, table=True):
    __tablename__ = "banker_edits"

    id: int | None = Field(default=None, primary_key=True)
    entry_id: str = Field(index=True)
    editor: str
    before: str
    after: str
    at: datetime = Field(sa_column=Column(DateTime(timezone=True)))


class AuditEventRow(SQLModel, table=True):
    """Audit log: who accessed or changed what, and when.

    Replaces the previous encrypted-file audit log (see app.audit) with a
    proper append-only database table — eliminates the O(n) rewrite per
    request and unbounded file growth.
    """
    __tablename__ = "audit_events"
    # Indexed on exactly the columns GET /api/audit filters and orders by.
    # Declared here rather than via Field(index=True) so the names match the
    # migration one-for-one; index=True would additionally emit a second,
    # auto-named index over the same column.
    __table_args__ = (
        Index("ix_audit_actor_email", "actor_email"),
        Index("ix_audit_action", "action"),
        Index("ix_audit_at", "at"),
    )

    event_id: str = Field(primary_key=True)
    at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    actor_user_id: str | None = None
    actor_email: str = "anonymous"
    actor_role: str | None = None
    method: str
    path: str
    status_code: int
    action: str
    resource_type: str
    resource_id: str | None = None
    outcome: str  # "success" | "denied" | "error"

