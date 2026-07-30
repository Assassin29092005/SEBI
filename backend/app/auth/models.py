"""User account shapes.

``User`` is the persisted record (password hash + salt included) — it must
never be returned from an API endpoint. ``UserPublic`` is the safe view.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.schema.models import Role

# SYSTEM is a provenance tag for machine-generated facts (see app.facts) —
# never a role a human account can hold.
LOGIN_ROLES: tuple[Role, ...] = (Role.PROMOTER, Role.AUDITOR, Role.BANKER)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class User(BaseModel):
    """The persisted record. Never serialise this straight into an API response."""

    user_id: str = Field(default_factory=lambda: str(uuid4()))
    email: str
    name: str
    role: Role
    password_hash: str
    password_salt: str
    disabled: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserPublic(BaseModel):
    """Safe-to-return view of a user — no password material."""

    user_id: str
    email: str
    name: str
    role: Role

    @classmethod
    def from_user(cls, user: User) -> UserPublic:
        return cls(user_id=user.user_id, email=user.email, name=user.name, role=user.role)


class RegisterRequest(BaseModel):
    # Length caps are a security bound, not a UX one: password hashing cost
    # (PBKDF2, 260k iterations — see security.py) scales with input size, so
    # an unbounded password is a cheap DoS lever against the server's CPU.
    email: str = Field(max_length=254)  # RFC 5321 mailbox length limit
    name: str = Field(max_length=200)
    password: str = Field(max_length=128)
    role: Role
    invite_code: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def email_looks_valid(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("not a valid email address")
        return v.strip().lower()

    @field_validator("name")
    @classmethod
    def name_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name is required")
        return v.strip()

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic
