"""Encrypted-at-rest document vault: archive/retrieve/list, tamper safety, path-traversal safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.intake.vault import archive_upload, list_archived_uploads, retrieve_upload
from app.schema.models import Role


@pytest.fixture(autouse=True)
def _uploads_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")


def test_archive_then_retrieve_round_trips_content_and_metadata() -> None:
    content = b"Issue Size: Rs 14.00 crore\nSME Exchange: NSE Emerge\n"
    meta = archive_upload(content, "bank_sanction_letter.txt", "text/plain", Role.PROMOTER)

    result = retrieve_upload(meta.document_id)
    assert result is not None
    retrieved_meta, retrieved_content = result
    assert retrieved_content == content
    assert retrieved_meta.filename == "bank_sanction_letter.txt"
    assert retrieved_meta.content_type == "text/plain"
    assert retrieved_meta.uploaded_by == Role.PROMOTER
    assert retrieved_meta.size_bytes == len(content)


def test_archived_file_on_disk_is_encrypted_not_plaintext() -> None:
    content = b"Litigation notice: Sunrise Agrotech Ltd vs. XYZ Traders, PAN ABCDE1234F"
    meta = archive_upload(content, "notice.pdf", "application/pdf", Role.AUDITOR)

    on_disk = (settings.uploads_dir / f"{meta.document_id}.enc").read_bytes()
    assert b"Sunrise Agrotech" not in on_disk
    assert b"PAN ABCDE1234F" not in on_disk
    assert b"notice.pdf" not in on_disk


def test_retrieve_missing_document_returns_none() -> None:
    assert retrieve_upload("no-such-id") is None


def test_retrieve_rejects_path_traversal_id() -> None:
    """A malicious ``document_id`` must never escape ``uploads_dir``."""
    assert retrieve_upload("../../../../etc/passwd") is None
    assert retrieve_upload("..\\..\\secrets") is None


def test_list_archived_uploads_is_sorted_oldest_first() -> None:
    first = archive_upload(b"one", "a.txt", "text/plain", Role.PROMOTER)
    second = archive_upload(b"two", "b.txt", "text/plain", Role.BANKER)

    listed = list_archived_uploads()
    assert [m.document_id for m in listed] == [first.document_id, second.document_id]


def test_list_archived_uploads_empty_when_dir_missing() -> None:
    assert list_archived_uploads() == []
