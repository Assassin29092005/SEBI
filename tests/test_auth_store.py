"""User account persistence: round-trip, case-insensitive lookup, duplicate-email rejection."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import store as auth_store
from app.auth.models import User
from app.auth.security import hash_password
from app.schema.models import Role


def _make_user(email: str = "promoter@example.com") -> User:
    salt, pw_hash = hash_password("Correct-Horse-Battery-Staple-1")
    return User(email=email, name="Test Promoter", role=Role.PROMOTER, password_hash=pw_hash, password_salt=salt)


async def test_created_user_survives_reload(db_session: AsyncSession) -> None:
    user = await auth_store.create(db_session, _make_user())

    assert await auth_store.get_by_id(db_session, user.user_id) == user
    # case-insensitive lookup
    assert await auth_store.get_by_email(db_session, "PROMOTER@example.com") == user


async def test_duplicate_email_rejected(db_session: AsyncSession) -> None:
    await auth_store.create(db_session, _make_user("dup@example.com"))
    with pytest.raises(ValueError, match="already registered"):
        await auth_store.create(db_session, _make_user("dup@example.com"))


async def test_missing_user_returns_none(db_session: AsyncSession) -> None:
    assert await auth_store.get_by_email(db_session, "nobody@example.com") is None
    assert await auth_store.get_by_id(db_session, "does-not-exist") is None
