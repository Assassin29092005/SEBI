"""Audit log: route classification, Postgres persistence, filtering."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import (
    AuditEvent,
    classify_request,
    list_audit_events,
    outcome_for_status,
    record_audit_event,
)
from app.schema.models import Role


def _event(**overrides: object) -> AuditEvent:
    defaults: dict[str, object] = {
        "actor_email": "someone@test.example",
        "method": "GET",
        "path": "/api/facts",
        "status_code": 200,
        "action": "view_facts",
        "resource_type": "fact",
        "outcome": "success",
    }
    defaults.update(overrides)
    return AuditEvent(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# classify_request
# --------------------------------------------------------------------------


def test_classify_confirm_fact_extracts_resource_id() -> None:
    assert classify_request("POST", "/api/facts/abc-123/confirm") == (
        "confirm_fact",
        "fact",
        "abc-123",
    )


def test_classify_download_document_extracts_resource_id() -> None:
    assert classify_request("GET", "/api/uploads/doc-1") == (
        "download_document",
        "document",
        "doc-1",
    )


def test_classify_document_page_view_is_distinct_from_download() -> None:
    """Both start /api/uploads/<id>; the page view must not fall through to
    the raw-path fallback, or the document id leaks into the action label."""
    assert classify_request("GET", "/api/uploads/doc-1/page/3") == (
        "view_document_page",
        "document",
        "doc-1",
    )


def test_classify_validation_kind_becomes_resource_id() -> None:
    assert classify_request("GET", "/api/validate/contradictions") == (
        "view_validation",
        "validation",
        "contradictions",
    )


def test_classify_hyphenated_validation_kind() -> None:
    """``/api/validate/lock-in`` must classify like every other validator."""
    assert classify_request("GET", "/api/validate/lock-in") == (
        "view_validation",
        "validation",
        "lock-in",
    )


def test_classify_review_advance_extracts_entry_id() -> None:
    assert classify_request("POST", "/api/review/general.cover_pages/advance") == (
        "advance_review",
        "review",
        "general.cover_pages",
    )


def test_classify_unmapped_route_falls_back_instead_of_dropping() -> None:
    action, resource_type, resource_id = classify_request("GET", "/api/does/not/exist")
    assert action == "GET /api/does/not/exist"
    assert resource_type == "unknown"
    assert resource_id is None


def test_classify_requires_matching_method() -> None:
    """A GET-shaped path posted to must not match the GET classifier entry."""
    action, resource_type, _ = classify_request("POST", "/api/facts")
    assert (action, resource_type) == ("add_fact", "fact")
    action, resource_type, _ = classify_request("GET", "/api/facts")
    assert (action, resource_type) == ("view_facts", "fact")


# --------------------------------------------------------------------------
# outcome_for_status
# --------------------------------------------------------------------------


def test_outcome_for_status_buckets() -> None:
    assert outcome_for_status(200) == "success"
    assert outcome_for_status(201) == "success"
    assert outcome_for_status(401) == "denied"
    assert outcome_for_status(403) == "denied"
    assert outcome_for_status(404) == "error"
    assert outcome_for_status(500) == "error"


# --------------------------------------------------------------------------
# Postgres-backed store: round trip, filtering, ordering
# --------------------------------------------------------------------------


async def test_record_then_read_round_trips(db_session: AsyncSession) -> None:
    recorded = await record_audit_event(
        db_session, _event(actor_email="promoter@test.example", actor_role=Role.PROMOTER)
    )

    events = await list_audit_events(db_session)
    assert [e.event_id for e in events] == [recorded.event_id]
    assert events[0].actor_role is Role.PROMOTER
    assert events[0].actor_email == "promoter@test.example"


async def test_list_events_filters_by_actor_action_resource_outcome(
    db_session: AsyncSession,
) -> None:
    await record_audit_event(
        db_session,
        _event(actor_email="a@x.com", action="view_facts", resource_type="fact"),
    )
    await record_audit_event(
        db_session,
        _event(
            actor_email="b@x.com",
            method="POST",
            path="/api/generate",
            status_code=403,
            action="generate_draft",
            resource_type="draft",
            outcome="denied",
        ),
    )

    assert len(await list_audit_events(db_session, actor_email="a@x.com")) == 1
    assert len(await list_audit_events(db_session, outcome="denied")) == 1
    assert len(await list_audit_events(db_session, resource_type="draft")) == 1
    assert len(await list_audit_events(db_session, action="view_facts")) == 1
    assert len(await list_audit_events(db_session)) == 2
    assert await list_audit_events(db_session, actor_email="nobody@x.com") == []


async def test_list_events_most_recent_first_and_respects_limit(
    db_session: AsyncSession,
) -> None:
    """Explicit timestamps: same-instant rows have no defined relative order."""
    base = datetime.now(UTC)
    first, second, third = [
        await record_audit_event(db_session, _event(at=base + timedelta(seconds=n)))
        for n in (0, 1, 2)
    ]

    events = await list_audit_events(db_session)
    assert [e.event_id for e in events] == [third.event_id, second.event_id, first.event_id]

    limited = await list_audit_events(db_session, limit=2)
    assert [e.event_id for e in limited] == [third.event_id, second.event_id]


async def test_record_never_raises_on_a_bad_event(db_session: AsyncSession) -> None:
    """Audit logging must not be able to take down the request it observes.

    A duplicate primary key is the cheapest way to force a DB-level failure;
    the contract is that it's swallowed and the caller carries on.
    """
    event = _event()
    await record_audit_event(db_session, event)
    await record_audit_event(db_session, event)  # same event_id — must not raise

    assert len(await list_audit_events(db_session)) == 1
