"""Audit log: who accessed or changed what, and when.

Extends the two audit mechanisms that already existed — fact provenance
(who *supplied* a value) and the banker review audit trail (who *edited* a
section) — to cover every request against the API: views, confirmations,
corrections, generation, exports, document downloads, logins, and access
denials. Compliance workflows need the "who looked at this" half of the
picture, not just the "who changed this" half.

Wired in as a single ASGI middleware in ``app.main`` (``audit_log_middleware``)
rather than a call scattered across 25+ endpoint handlers: every request is
classified by ``classify_request`` (method + path -> human-readable action /
resource type, with the resource id pulled out of the path where one exists)
and recorded here.

Storage: Postgres ``audit_events`` table (see ``app.db_models.AuditEventRow``).
Replaced the previous encrypted-file approach which had an O(n) rewrite per
request — exactly the kind of thing a real database's append-only table
exists to solve.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db_models import AuditEventRow
from app.schema.models import Role

logger = logging.getLogger("drhp.audit")

Outcome = Literal["success", "denied", "error"]


def outcome_for_status(status_code: int) -> Outcome:
    if status_code in (401, 403):
        return "denied"
    if status_code >= 400:
        return "error"
    return "success"


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # None/"anonymous" when the request carried no valid token (public
    # endpoints, or a request that was itself rejected before authenticating).
    actor_user_id: str | None = None
    actor_email: str = "anonymous"
    actor_role: Role | None = None
    method: str
    path: str
    status_code: int
    action: str
    resource_type: str
    resource_id: str | None = None
    outcome: Outcome


# --------------------------------------------------------------------------
# Route -> human-readable (action, resource_type) classifier
# --------------------------------------------------------------------------
# Ordered, most-specific first. A path segment named ``id`` in the regex
# becomes ``resource_id``. Anything not matched here still gets logged (see
# classify_request's fallback) — an unmapped route is a documentation gap,
# not a reason to silently drop the audit record.

_ROUTES: list[tuple[str, re.Pattern[str], str, str]] = [
    ("POST", re.compile(r"^/api/auth/register$"), "register", "auth"),
    ("POST", re.compile(r"^/api/auth/login$"), "login", "auth"),
    ("GET", re.compile(r"^/api/auth/me$"), "view_own_account", "auth"),
    ("GET", re.compile(r"^/api/schema$"), "view_schema", "schema"),
    ("GET", re.compile(r"^/api/schema/clause/"), "view_clause_text", "schema"),
    ("POST", re.compile(r"^/api/eligibility$"), "check_eligibility", "eligibility"),
    ("GET", re.compile(r"^/api/wizard/questions$"), "view_wizard_questions", "wizard"),
    ("GET", re.compile(r"^/api/facts$"), "view_facts", "fact"),
    ("POST", re.compile(r"^/api/facts$"), "add_fact", "fact"),
    ("POST", re.compile(r"^/api/facts/(?P<id>[^/]+)/confirm$"), "confirm_fact", "fact"),
    ("POST", re.compile(r"^/api/facts/(?P<id>[^/]+)/correct$"), "correct_fact", "fact"),
    ("POST", re.compile(r"^/api/uploads/extract$"), "upload_document", "document"),
    ("GET", re.compile(r"^/api/uploads$"), "view_documents", "document"),
    # Must precede the download entry: both start /api/uploads/<id>, and the
    # download pattern is anchored with $ so only this one matches the
    # page-image path.
    (
        "GET",
        re.compile(r"^/api/uploads/(?P<id>[^/]+)/page/\d+$"),
        "view_document_page",
        "document",
    ),
    ("GET", re.compile(r"^/api/uploads/(?P<id>[^/]+)$"), "download_document", "document"),
    ("POST", re.compile(r"^/api/proposals/accept$"), "accept_proposal", "fact"),
    ("GET", re.compile(r"^/api/litigation$"), "search_litigation", "litigation"),
    ("POST", re.compile(r"^/api/generate$"), "generate_draft", "draft"),
    ("GET", re.compile(r"^/api/sections$"), "view_draft", "draft"),
    ("GET", re.compile(r"^/api/validate/(?P<id>[\w-]+)$"), "view_validation", "validation"),
    ("POST", re.compile(r"^/api/validate/"), "run_validation", "validation"),
    ("GET", re.compile(r"^/api/coverage/benchmark$"), "view_coverage_benchmark", "coverage"),
    ("GET", re.compile(r"^/api/coverage$"), "view_coverage", "coverage"),
    ("GET", re.compile(r"^/api/gaps$"), "view_gaps", "gap_report"),
    ("GET", re.compile(r"^/api/review/state$"), "view_review_state", "review"),
    ("POST", re.compile(r"^/api/review/(?P<id>[^/]+)/advance$"), "advance_review", "review"),
    ("POST", re.compile(r"^/api/review/edit$"), "edit_section", "review"),
    ("POST", re.compile(r"^/api/review/export$"), "export_package", "export"),
    ("GET", re.compile(r"^/api/assemble/(?P<id>[^/]+)$"), "download_docx", "export"),
    ("GET", re.compile(r"^/api/export/bundle$"), "download_bundle", "export"),
    ("GET", re.compile(r"^/api/audit$"), "view_audit_log", "audit"),
    ("GET", re.compile(r"^/api/metrics$"), "view_metrics", "monitoring"),
]


def classify_request(method: str, path: str) -> tuple[str, str, str | None]:
    """Returns ``(action, resource_type, resource_id)`` for a request.

    Falls back to a raw ``"METHOD /path"`` action / ``"unknown"`` resource
    type for anything not in the table above — new endpoints still get
    audited, just without a friendly label until the table is updated.
    """
    for route_method, pattern, action, resource_type in _ROUTES:
        if route_method != method:
            continue
        match = pattern.match(path)
        if match:
            return action, resource_type, match.groupdict().get("id")
    return f"{method} {path}", "unknown", None


# --------------------------------------------------------------------------
# Postgres-backed audit repository
# --------------------------------------------------------------------------


def _to_row(event: AuditEvent) -> AuditEventRow:
    return AuditEventRow(
        event_id=event.event_id,
        at=event.at,
        actor_user_id=event.actor_user_id,
        actor_email=event.actor_email,
        actor_role=event.actor_role.value if event.actor_role else None,
        method=event.method,
        path=event.path,
        status_code=event.status_code,
        action=event.action,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        outcome=event.outcome,
    )


def _to_event(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        event_id=row.event_id,
        at=row.at,
        actor_user_id=row.actor_user_id,
        actor_email=row.actor_email,
        actor_role=Role(row.actor_role) if row.actor_role else None,
        method=row.method,
        path=row.path,
        status_code=row.status_code,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        outcome=row.outcome,
    )


async def record_audit_event(session: AsyncSession, event: AuditEvent) -> AuditEvent:
    """Append an audit event to the database. Never raises — audit logging
    must not break the request it's observing."""
    try:
        session.add(_to_row(event))
        await session.commit()
    except Exception:  # noqa: BLE001 — audit logging must never break
        logger.exception("Failed to record audit event %s", event.event_id)
        await session.rollback()
    return event


async def list_audit_events(
    session: AsyncSession,
    *,
    actor_email: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    outcome: Outcome | None = None,
    limit: int = 500,
) -> list[AuditEvent]:
    """Most recent first, optionally filtered."""
    stmt = select(AuditEventRow)

    if actor_email:
        stmt = stmt.where(AuditEventRow.actor_email == actor_email)
    if action:
        stmt = stmt.where(AuditEventRow.action == action)
    if resource_type:
        stmt = stmt.where(AuditEventRow.resource_type == resource_type)
    if outcome:
        stmt = stmt.where(AuditEventRow.outcome == outcome)

    stmt = stmt.order_by(desc(AuditEventRow.at)).limit(limit)

    rows = (await session.execute(stmt)).scalars().all()
    return [_to_event(row) for row in rows]
