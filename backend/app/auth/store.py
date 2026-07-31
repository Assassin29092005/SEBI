"""User store: in-memory, atomically persisted to disk, encrypted at rest.

Mirrors the pattern in :mod:`app.persistence` (write to a ``.tmp`` sibling,
then ``os.replace``, encrypted via :mod:`app.crypto`) so a crash mid-write
can never leave a torn users file and account emails/names never sit on disk
as plaintext. Password hashes were already salted+hashed, never plaintext —
encryption here protects the PII (email, name) around them. A real
deployment would swap this for a real database with proper migrations and
per-tenant isolation — documented, and the storage interface below is narrow
enough to swap without touching callers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import BaseModel, ValidationError

from app.auth.models import User
from app.config import settings
from app.crypto import DecryptionError, decrypt_bytes, encrypt_bytes

logger = logging.getLogger("drhp.auth")

# ``.enc`` (not ``.json``): the file is ciphertext, not readable JSON.
USERS_FILENAME = "users.enc"


class _UsersFile(BaseModel):
    users: list[User] = []


class UserStore:
    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory
        self._by_id: dict[str, User] = {}
        self._by_email: dict[str, str] = {}  # lowercased email -> user_id
        self._load()

    def _path(self) -> Path:
        base = self._directory if self._directory is not None else settings.auth_dir
        return base / USERS_FILENAME

    def _load(self) -> None:
        path = self._path()
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("User store at %s unreadable, starting empty: %s", path, exc)
            return
        try:
            plaintext = decrypt_bytes(raw)
        except DecryptionError as exc:
            logger.warning("User store at %s could not be decrypted, starting empty: %s", path, exc)
            return
        try:
            data = _UsersFile.model_validate_json(plaintext)
        except ValidationError as exc:
            logger.warning("User store at %s is corrupt, starting empty: %s", path, exc)
            return
        for user in data.users:
            self._by_id[user.user_id] = user
            self._by_email[user.email.lower()] = user.user_id

    def _save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = _UsersFile(users=list(self._by_id.values()))
        plaintext = payload.model_dump_json(indent=2).encode("utf-8")
        tmp.write_bytes(encrypt_bytes(plaintext))
        os.replace(tmp, path)

    def get_by_email(self, email: str) -> User | None:
        user_id = self._by_email.get(email.lower())
        return self._by_id.get(user_id) if user_id else None

    def get_by_id(self, user_id: str) -> User | None:
        return self._by_id.get(user_id)

    def create(self, user: User) -> User:
        if user.email.lower() in self._by_email:
            raise ValueError(f"email already registered: {user.email}")
        self._by_id[user.user_id] = user
        self._by_email[user.email.lower()] = user.user_id
        self._save()
        return user


_store: UserStore | None = None


def get_user_store() -> UserStore:
    """Process-wide singleton, lazily created (and lazily loaded from disk)."""
    global _store
    if _store is None:
        _store = UserStore()
    return _store


def reset_user_store() -> UserStore:
    """Swap in a fresh store — used by tests after monkeypatching ``settings.auth_dir``."""
    global _store
    _store = UserStore()
    return _store
