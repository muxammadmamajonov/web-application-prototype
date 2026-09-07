"""(#133) Fernet key rotation: encrypt under the primary SECRET_KEY, decrypt under
the primary OR any SECRET_KEY_PREVIOUS, and rotate_secret re-encrypts to the primary
so the old key can eventually be retired."""
from __future__ import annotations

from app.config import settings
from app.core import crypto

OLD = "old-secret-key-aaaaaaaaaaaaaaaaaaaaaaaa"   # >= 32 chars
NEW = "new-secret-key-bbbbbbbbbbbbbbbbbbbbbbbb"


def _use(secret: str, previous: str | None) -> None:
    settings.secret_key = secret
    settings.secret_key_previous = previous
    crypto._multifernet.cache_clear()


def test_key_rotation() -> None:
    orig_key, orig_prev = settings.secret_key, settings.secret_key_previous
    try:
        # Encrypt under the OLD key.
        _use(OLD, None)
        token = crypto.encrypt_secret("hunter2")
        assert crypto.decrypt_secret(token) == "hunter2"

        # Rotate in NEW as primary, OLD as previous (decrypt-only).
        _use(NEW, OLD)
        assert crypto.decrypt_secret(token) == "hunter2"          # old token still readable
        fresh = crypto.encrypt_secret("fresh")                    # new writes use NEW
        assert crypto.decrypt_secret(fresh) == "fresh"

        # Re-encrypt the old token to NEW, then retire OLD.
        rotated = crypto.rotate_secret(token)
        _use(NEW, None)
        assert crypto.decrypt_secret(rotated) == "hunter2"        # re-encrypted → readable
        assert crypto.decrypt_secret(token) is None               # original now unreadable (fail-closed)
    finally:
        settings.secret_key, settings.secret_key_previous = orig_key, orig_prev
        crypto._multifernet.cache_clear()
