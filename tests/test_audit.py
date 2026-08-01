"""Audit log: route classification, encrypted storage, filtering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.audit import AUDIT_FILENAME, AuditEvent, AuditLog, classify_request, outcome_for_status
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


def test_classify_validation_kind_becomes_resource_id() -> None:
    assert classify_request("GET", "/api/validate/contradictions") == (
        "view_validation",
        "validation",
        "contradictions",
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
# AuditLog: round trip, encryption at rest, filtering, corruption safety
# --------------------------------------------------------------------------


def test_record_then_reload_round_trips(tmp_path: Path) -> None:
    log = AuditLog(directory=tmp_path)
    recorded = log.record(_event(actor_email="promoter@test.example", actor_role=Role.PROMOTER))

    reloaded = AuditLog(directory=tmp_path)
    events = reloaded.list_events()
    assert events == [recorded]


def test_file_on_disk_is_encrypted_not_plaintext(tmp_path: Path) -> None:
    log = AuditLog(directory=tmp_path)
    log.record(
        _event(
            actor_email="sensitive.promoter@sunriseagrotech.example",
            path="/api/uploads/dd-certificate-id",
        )
    )
    raw = (tmp_path / AUDIT_FILENAME).read_bytes()
    assert b"sensitive.promoter" not in raw
    assert b"dd-certificate-id" not in raw
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)


def test_list_events_filters_by_actor_action_resource_outcome(tmp_path: Path) -> None:
    log = AuditLog(directory=tmp_path)
    log.record(_event(actor_email="a@x.com", action="view_facts", resource_type="fact", outcome="success"))
    log.record(
        _event(
            actor_email="b@x.com",
            method="POST",
            path="/api/generate",
            status_code=403,
            action="generate_draft",
            resource_type="draft",
            outcome="denied",
        )
    )

    assert len(log.list_events(actor_email="a@x.com")) == 1
    assert len(log.list_events(outcome="denied")) == 1
    assert len(log.list_events(resource_type="draft")) == 1
    assert len(log.list_events(action="view_facts")) == 1
    assert len(log.list_events()) == 2
    assert log.list_events(actor_email="nobody@x.com") == []


def test_list_events_most_recent_first_and_respects_limit(tmp_path: Path) -> None:
    log = AuditLog(directory=tmp_path)
    first = log.record(_event())
    second = log.record(_event())
    third = log.record(_event())

    events = log.list_events()
    assert [e.event_id for e in events] == [third.event_id, second.event_id, first.event_id]

    limited = log.list_events(limit=2)
    assert [e.event_id for e in limited] == [third.event_id, second.event_id]


def test_missing_directory_starts_empty(tmp_path: Path) -> None:
    log = AuditLog(directory=tmp_path / "does_not_exist")
    assert log.list_events() == []


def test_corrupt_file_starts_empty_without_raising(tmp_path: Path) -> None:
    (tmp_path / AUDIT_FILENAME).write_bytes(b"not encrypted, not json, just garbage")
    log = AuditLog(directory=tmp_path)
    assert log.list_events() == []
