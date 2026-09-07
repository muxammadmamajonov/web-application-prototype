"""H2 — provider secret encryption at rest."""
from __future__ import annotations

from app.core.crypto import decrypt_secret, encrypt_secret, is_encrypted


def test_encrypt_decrypt_roundtrip() -> None:
    secret = "click_super_secret_key_123"
    token = encrypt_secret(secret)
    assert token.startswith("enc:")
    assert token != secret
    assert decrypt_secret(token) == secret


def test_legacy_plaintext_passthrough() -> None:
    # A value stored before encryption (no enc: prefix) reads back unchanged.
    assert decrypt_secret("legacy_plaintext_secret") == "legacy_plaintext_secret"


def test_none_and_empty() -> None:
    assert decrypt_secret(None) is None
    assert decrypt_secret("") == ""


def test_is_encrypted() -> None:
    assert is_encrypted(encrypt_secret("x")) is True
    assert is_encrypted("plaintext") is False
    assert is_encrypted(None) is False


def test_corrupt_token_fails_closed() -> None:
    # An enc:-prefixed but invalid token returns None (treated as "no secret").
    assert decrypt_secret("enc:not-a-valid-fernet-token") is None
