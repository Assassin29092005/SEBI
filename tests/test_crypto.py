"""Encryption at rest: app.crypto round-trip, tamper/wrong-key detection."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.crypto import DecryptionError, decrypt_bytes, encrypt_bytes


def test_round_trip_recovers_the_original_bytes() -> None:
    plaintext = b"Sunrise Agrotech Ltd -- PAN ABCDE1234F, issue size Rs 12.5 crore"
    ciphertext = encrypt_bytes(plaintext)
    assert ciphertext != plaintext
    assert decrypt_bytes(ciphertext) == plaintext


def test_ciphertext_does_not_contain_the_plaintext_substring() -> None:
    plaintext = b"a very identifiable secret string 12345"
    ciphertext = encrypt_bytes(plaintext)
    assert b"identifiable secret" not in ciphertext


def test_decrypting_garbage_raises_decryption_error() -> None:
    with pytest.raises(DecryptionError):
        decrypt_bytes(b"not a valid fernet token at all")


def test_decrypting_with_the_wrong_key_raises_decryption_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    ciphertext = encrypt_bytes(b"a session snapshot's worth of facts")

    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    with pytest.raises(DecryptionError):
        decrypt_bytes(ciphertext)


def test_two_encryptions_of_the_same_plaintext_differ() -> None:
    """Fernet includes a random IV — ciphertext must not be deterministic
    (a fixed mapping would leak which facts repeat across the store)."""
    plaintext = b"repeated value"
    assert encrypt_bytes(plaintext) != encrypt_bytes(plaintext)
