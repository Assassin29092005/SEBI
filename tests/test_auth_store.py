"""User store: round-trip persistence, encryption at rest, corruption safety."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.auth.models import User
from app.auth.security import hash_password
from app.auth.store import USERS_FILENAME, UserStore
from app.schema.models import Role


def _make_user(email: str = "promoter@example.com") -> User:
    salt, pw_hash = hash_password("Correct-Horse-Battery-Staple-1")
    return User(email=email, name="Test Promoter", role=Role.PROMOTER, password_hash=pw_hash, password_salt=salt)


def test_created_user_survives_reload(tmp_path: Path) -> None:
    store = UserStore(directory=tmp_path)
    user = store.create(_make_user())

    reloaded = UserStore(directory=tmp_path)
    assert reloaded.get_by_id(user.user_id) == user
    assert reloaded.get_by_email("PROMOTER@example.com") == user  # case-insensitive lookup


def test_duplicate_email_rejected(tmp_path: Path) -> None:
    store = UserStore(directory=tmp_path)
    store.create(_make_user("dup@example.com"))
    with pytest.raises(ValueError, match="already registered"):
        store.create(_make_user("dup@example.com"))


def test_user_store_file_on_disk_is_encrypted_not_plaintext(tmp_path: Path) -> None:
    """Emails, names, and password hashes must not be readable by opening the file."""
    store = UserStore(directory=tmp_path)
    store.create(_make_user("sensitive.promoter@sunriseagrotech.example"))

    raw = (tmp_path / USERS_FILENAME).read_bytes()
    assert b"sensitive.promoter" not in raw
    assert b"Test Promoter" not in raw
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)


def test_missing_store_file_starts_empty(tmp_path: Path) -> None:
    store = UserStore(directory=tmp_path / "does_not_exist")
    assert store.get_by_email("nobody@example.com") is None


def test_corrupt_store_file_starts_empty_without_raising(tmp_path: Path) -> None:
    (tmp_path / USERS_FILENAME).write_bytes(b"not encrypted, not json, just garbage")
    store = UserStore(directory=tmp_path)
    assert store.get_by_email("nobody@example.com") is None
