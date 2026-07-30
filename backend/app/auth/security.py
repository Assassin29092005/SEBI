"""Password hashing and JWT issuing/verification.

No new native-extension dependency: password hashing is stdlib
``hashlib.pbkdf2_hmac`` (salted, 260k iterations — OWASP's current minimum
for PBKDF2-SHA256) rather than bcrypt/argon2, so there is nothing to compile
on a judge's or a promoter's machine. JWTs are signed with HS256 via PyJWT.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import settings

logger = logging.getLogger("drhp.auth")

_PBKDF2_ITERATIONS = 260_000

# Fallback signing key when JWT_SECRET_KEY is unset (local dev / first run).
# Generated once per process — tokens issued before a restart stop verifying
# after one, which is the correct fail-safe: production sets JWT_SECRET_KEY.
_EPHEMERAL_SECRET = secrets.token_hex(32)
_warned_ephemeral = False


def _effective_secret() -> str:
    global _warned_ephemeral
    if settings.jwt_secret_key:
        return settings.jwt_secret_key
    if not _warned_ephemeral:
        logger.warning(
            "JWT_SECRET_KEY is not set — using a per-process random key. "
            "All issued tokens will stop verifying on the next restart. "
            "Set JWT_SECRET_KEY in .env before deploying for real use."
        )
        _warned_ephemeral = True
    return _EPHEMERAL_SECRET


def hash_password(password: str) -> tuple[str, str]:
    """Returns ``(salt_hex, hash_hex)``."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ITERATIONS
    )
    return hmac.compare_digest(digest.hex(), expected_hash_hex)


class InvalidToken(Exception):
    """Raised for any unparseable, unsigned, or expired token."""


def create_access_token(*, user_id: str, role: str, email: str) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
    }
    return jwt.encode(payload, _effective_secret(), algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _effective_secret(), algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc
