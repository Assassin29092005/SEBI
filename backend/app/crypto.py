"""Encryption at rest: the session snapshot, the user store, and archived
uploaded documents (see ``app.persistence``, ``app.auth.store``,
``app.intake.vault``) are all written and read through this module — none of
them touch the filesystem directly.

Fernet (AES-128-CBC + HMAC-SHA256, authenticated symmetric encryption) keyed
by ``Settings.encryption_key``. Same fallback pattern as
``app.auth.security``'s JWT secret: an unset key logs one warning and falls
back to a per-process random key, so local dev and the test suite still
round-trip correctly with zero setup — but anything encrypted under the
ephemeral key is unreadable after a restart (the key is gone), which is the
correct fail-safe. Production deployments must set ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger("drhp.crypto")

_EPHEMERAL_KEY = Fernet.generate_key()
_warned_ephemeral = False


class DecryptionError(Exception):
    """Ciphertext could not be decrypted — wrong/missing key, or corrupt data.

    Callers treat this the same way ``app.persistence`` treats a corrupt
    snapshot: log a warning and start fresh rather than crash the app.
    """


def _effective_key() -> bytes:
    global _warned_ephemeral
    if settings.encryption_key:
        return settings.encryption_key.encode("utf-8")
    if not _warned_ephemeral:
        logger.warning(
            "ENCRYPTION_KEY is not set — using a per-process random key. "
            "Data encrypted now becomes unreadable after any restart. "
            "Set ENCRYPTION_KEY in .env before storing real data — generate "
            "one with: python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"'
        )
        _warned_ephemeral = True
    return _EPHEMERAL_KEY


def _fernet() -> Fernet:
    key = _effective_key()
    try:
        return Fernet(key)
    except ValueError as exc:
        # Fernet keys are exactly 32 url-safe base64 bytes — a misconfigured
        # ENCRYPTION_KEY should fail loudly and immediately, not corrupt data.
        raise ValueError(
            "ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        ) from exc


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    try:
        return _fernet().decrypt(token)
    except InvalidToken as exc:
        raise DecryptionError(
            "could not decrypt — wrong/missing ENCRYPTION_KEY, or the data is corrupt"
        ) from exc
