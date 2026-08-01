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
and recorded here, encrypted at rest like the rest of this app's storage
(see :mod:`app.crypto`).

Storage note: this rewrites the whole encrypted file on every event
(read-modify-write, atomic tmp-then-``os.replace`` — the same pattern
``app.auth.store`` used before facts/review/users moved to Postgres, see
``app.db``). That's fine for a single issuer's audit volume over a drafting
cycle, but it is an O(n) write on every request — exactly the kind of thing
a real database's append-only table exists to solve, same problem Postgres
was brought in to fix for the rest of this app's durable state. Documented
as a known limitation, not (yet) migrated.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import settings
from app.crypto import DecryptionError, decrypt_bytes, encrypt_bytes
from app.schema.models import Role

logger = logging.getLogger("drhp.audit")

# ``.enc`` (not ``.json``): the file is ciphertext, not readable JSON.
AUDIT_FILENAME = "audit.enc"

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
    ("POST", re.compile(r"^/api/eligibility$"), "check_eligibility", "eligibility"),
    ("GET", re.compile(r"^/api/wizard/questions$"), "view_wizard_questions", "wizard"),
    ("GET", re.compile(r"^/api/facts$"), "view_facts", "fact"),
    ("POST", re.compile(r"^/api/facts$"), "add_fact", "fact"),
    ("POST", re.compile(r"^/api/facts/(?P<id>[^/]+)/confirm$"), "confirm_fact", "fact"),
    ("POST", re.compile(r"^/api/facts/(?P<id>[^/]+)/correct$"), "correct_fact", "fact"),
    ("POST", re.compile(r"^/api/uploads/extract$"), "upload_document", "document"),
    ("GET", re.compile(r"^/api/uploads$"), "view_documents", "document"),
    ("GET", re.compile(r"^/api/uploads/(?P<id>[^/]+)$"), "download_document", "document"),
    ("POST", re.compile(r"^/api/proposals/accept$"), "accept_proposal", "fact"),
    ("GET", re.compile(r"^/api/litigation$"), "search_litigation", "litigation"),
    ("POST", re.compile(r"^/api/generate$"), "generate_draft", "draft"),
    ("GET", re.compile(r"^/api/sections$"), "view_draft", "draft"),
    ("GET", re.compile(r"^/api/validate/(?P<id>\w+)$"), "view_validation", "validation"),
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
# Storage: encrypted, atomic, read-modify-write on every event (see the
# module docstring's Storage note for the O(n)-per-write caveat)
# --------------------------------------------------------------------------


class _AuditFile(BaseModel):
    events: list[AuditEvent] = []


class AuditLog:
    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory
        self._events: list[AuditEvent] = []
        self._load()

    def _path(self) -> Path:
        base = self._directory if self._directory is not None else settings.audit_dir
        return base / AUDIT_FILENAME

    def _load(self) -> None:
        path = self._path()
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("Audit log at %s unreadable, starting empty: %s", path, exc)
            return
        try:
            plaintext = decrypt_bytes(raw)
        except DecryptionError as exc:
            logger.warning("Audit log at %s could not be decrypted, starting empty: %s", path, exc)
            return
        try:
            data = _AuditFile.model_validate_json(plaintext)
        except ValueError as exc:
            logger.warning("Audit log at %s is corrupt, starting empty: %s", path, exc)
            return
        self._events = list(data.events)

    def _save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        plaintext = _AuditFile(events=self._events).model_dump_json().encode("utf-8")
        tmp.write_bytes(encrypt_bytes(plaintext))
        os.replace(tmp, path)

    def record(self, event: AuditEvent) -> AuditEvent:
        self._events.append(event)
        self._save()
        return event

    def list_events(
        self,
        *,
        actor_email: str | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        outcome: Outcome | None = None,
        limit: int = 500,
    ) -> list[AuditEvent]:
        """Most recent first, optionally filtered. ``limit`` caps the response size."""
        results = self._events
        if actor_email:
            results = [e for e in results if e.actor_email == actor_email]
        if action:
            results = [e for e in results if e.action == action]
        if resource_type:
            results = [e for e in results if e.resource_type == resource_type]
        if outcome:
            results = [e for e in results if e.outcome == outcome]
        return sorted(results, key=lambda e: e.at, reverse=True)[:limit]


_log: AuditLog | None = None


def get_audit_log() -> AuditLog:
    """Process-wide singleton, lazily created (and lazily loaded from disk)."""
    global _log
    if _log is None:
        _log = AuditLog()
    return _log


def reset_audit_log() -> AuditLog:
    """Swap in a fresh log — used by tests after monkeypatching ``settings.audit_dir``."""
    global _log
    _log = AuditLog()
    return _log
