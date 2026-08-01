"""User account persistence — Postgres-backed.

Async repository functions over :class:`app.db_models.UserRow`, translating
to/from the API-facing :class:`app.auth.models.User` Pydantic model. Callers
(``app.auth.router``, ``app.auth.dependencies``) take a
``session: AsyncSession = Depends(get_session)`` and ``await`` these.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.auth.models import User
from app.db_models import UserRow


def _to_user(row: UserRow) -> User:
    return User(
        user_id=row.user_id,
        email=row.email,
        name=row.name,
        role=row.role,
        password_hash=row.password_hash,
        password_salt=row.password_salt,
        disabled=row.disabled,
        created_at=row.created_at,
    )


def _to_row(user: User) -> UserRow:
    return UserRow(
        user_id=user.user_id,
        email=user.email.lower(),
        name=user.name,
        role=user.role.value,
        password_hash=user.password_hash,
        password_salt=user.password_salt,
        disabled=user.disabled,
        created_at=user.created_at,
    )


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    row = (
        await session.execute(select(UserRow).where(UserRow.email == email.lower()))
    ).scalar_one_or_none()
    return _to_user(row) if row is not None else None


async def get_by_id(session: AsyncSession, user_id: str) -> User | None:
    row = await session.get(UserRow, user_id)
    return _to_user(row) if row is not None else None


async def create(session: AsyncSession, user: User) -> User:
    """Raises ``ValueError`` on a duplicate (case-insensitive) email."""
    session.add(_to_row(user))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError(f"email already registered: {user.email}") from exc
    return user
