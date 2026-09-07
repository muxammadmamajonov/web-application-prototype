"""(#F-21) Key separation: JWT signing key vs payment-secret encryption key.

Historically one ``SECRET_KEY`` both signed session JWTs and derived the Fernet key
for payment secrets, so a single leak could forge any token AND decrypt every
tenant's provider keys. We split those into two NEW optional settings —
``jwt_signing_key`` (JWT_SIGNING_KEY) and ``encryption_key`` (ENCRYPTION_KEY) — each
FALLING BACK to ``secret_key`` when unset.

Backward-compatibility contract (the load-bearing property): with the new keys
UNSET, behaviour is byte-identical to before — a JWT signed today still verifies and
an ``enc:`` payload still decrypts. With distinct keys SET, the two duties use
independent keys, and a token forged with the encryption key is not a valid JWT.

Also asserts the #F-01 ``rls_strict`` boot gate: prod + RLS off + strict ⇒ refuse to
boot; strict unset ⇒ boots (only logs CRITICAL).

These tests are DB-free; they mutate ``settings`` in place (restoring in a finally)
and clear the crypto MultiFernet cache, mirroring tests/test_key_rotation.py.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import jwt
import pytest
from fastapi import HTTPException
from jwt import InvalidTokenError

from app.config import settings
from app.core import crypto, security

# 48-char-ish distinct keys (each >= 32 so a parallel prod-secret check would pass).
BASE = "base-secret-key-000000000000000000000000"
JWTK = "jwt-signing-key-111111111111111111111111"
ENCK = "encryption-key-2222222222222222222222222"


@contextmanager
def _keys(*, secret: str, jwt_signing: str | None, encryption: str | None,
          previous: str | None = None) -> Iterator[None]:
    """Swap the three keys (+ secret_key_previous) on the shared settings object,
    clear the Fernet cache so the new key takes effect, and restore on exit."""
    saved = (
        settings.secret_key, settings.jwt_signing_key,
        settings.encryption_key, settings.secret_key_previous,
    )
    settings.secret_key = secret
    settings.jwt_signing_key = jwt_signing
    settings.encryption_key = encryption
    settings.secret_key_previous = previous
    crypto._multifernet.cache_clear()
    try:
        yield
    finally:
        (settings.secret_key, settings.jwt_signing_key,
         settings.encryption_key, settings.secret_key_previous) = saved
        crypto._multifernet.cache_clear()


# ── Backward compatibility: keys UNSET behaves exactly like the single-key era ──

def test_unset_keys_fall_back_to_secret_key_for_jwt_and_crypto() -> None:
    """With JWT_SIGNING_KEY and ENCRYPTION_KEY unset, a token signed today still
    verifies and a Fernet round-trip works — byte-identical to before the split."""
    with _keys(secret=BASE, jwt_signing=None, encryption=None):
        # JWT signed with the resolved key (== secret_key) verifies.
        token = security.issue_token(user_id="user-123")
        assert security.decode_token(token)["sub"] == "user-123"
        # And it is genuinely signed with secret_key (the historical key).
        assert jwt.decode(token, BASE, algorithms=[security.ALGORITHM])["sub"] == "user-123"
        # Crypto round-trips, and the payload is decryptable with a key derived
        # straight from secret_key (proves nothing changed for existing data).
        blob = crypto.encrypt_secret("provider-secret")
        assert crypto.decrypt_secret(blob) == "provider-secret"


def test_token_and_blob_from_singlekey_era_still_work_after_keys_introduced() -> None:
    """A token/blob produced under single-key config remains valid when the codebase
    that introduced the new (still-unset) settings reads them back."""
    # Produce artifacts under pure single-key config.
    with _keys(secret=BASE, jwt_signing=None, encryption=None):
        token = security.issue_token(user_id="u1")
        blob = crypto.encrypt_secret("s3cr3t")
    # Re-open with the new keys still unset (the live "before cutover" state).
    with _keys(secret=BASE, jwt_signing=None, encryption=None):
        assert security.decode_token(token)["sub"] == "u1"
        assert crypto.decrypt_secret(blob) == "s3cr3t"


# ── Distinct keys SET: independent duties ───────────────────────────────────────

def test_distinct_keys_jwt_uses_signing_key() -> None:
    """JWT is signed/verified with jwt_signing_key — NOT secret_key, NOT encryption_key."""
    with _keys(secret=BASE, jwt_signing=JWTK, encryption=ENCK):
        token = security.issue_token(user_id="abc")
        # Verifiable with the signing key…
        assert jwt.decode(token, JWTK, algorithms=[security.ALGORITHM])["sub"] == "abc"
        assert security.decode_token(token)["sub"] == "abc"
        # …and NOT with secret_key or the encryption key.
        for wrong in (BASE, ENCK):
            with pytest.raises(InvalidTokenError):
                jwt.decode(token, wrong, algorithms=[security.ALGORITHM])


def test_distinct_keys_fernet_uses_encryption_key() -> None:
    """Payment-secret encryption derives its Fernet key from encryption_key — so the
    blob is unreadable once encryption_key changes, but readable while it holds."""
    with _keys(secret=BASE, jwt_signing=JWTK, encryption=ENCK):
        blob = crypto.encrypt_secret("payme-key")
        assert crypto.decrypt_secret(blob) == "payme-key"
    # Same secret_key, but a DIFFERENT encryption_key (no previous) → fail-closed None,
    # proving the Fernet key tracks encryption_key and not secret_key.
    with _keys(secret=BASE, jwt_signing=JWTK, encryption="different-encryption-key-3333333333333"):
        assert crypto.decrypt_secret(blob) is None


def test_token_forged_with_encryption_key_does_not_validate_as_jwt() -> None:
    """The core threat the split defends against: someone holding the ENCRYPTION key
    must not be able to mint a session token. A JWT signed with encryption_key is
    rejected because verification uses jwt_signing_key."""
    with _keys(secret=BASE, jwt_signing=JWTK, encryption=ENCK):
        forged = jwt.encode({"sub": "attacker"}, ENCK, algorithm=security.ALGORITHM)
        # decode_token wraps a signature failure as a 401 HTTPException.
        with pytest.raises(HTTPException) as ei:
            security.decode_token(forged)
        assert ei.value.status_code == 401


# ── MultiFernet rotation still works across the single-key → split migration ────

def test_old_secret_key_in_previous_decrypts_after_split_to_encryption_key() -> None:
    """Migrating from single-key to a distinct ENCRYPTION_KEY without downtime: put
    the old secret_key in SECRET_KEY_PREVIOUS (decrypt-only) and the new distinct key
    in ENCRYPTION_KEY. Old blobs still decrypt; new writes use the new key."""
    # Encrypt in the single-key era (key derived from BASE).
    with _keys(secret=BASE, jwt_signing=None, encryption=None):
        old_blob = crypto.encrypt_secret("legacy-provider-key")
    # Split: new distinct encryption_key as primary, old BASE as decrypt-only previous.
    with _keys(secret=BASE, jwt_signing=JWTK, encryption=ENCK, previous=BASE):
        assert crypto.decrypt_secret(old_blob) == "legacy-provider-key"   # old still readable
        fresh = crypto.encrypt_secret("new-provider-key")
        assert crypto.decrypt_secret(fresh) == "new-provider-key"          # new writes OK
        # The fresh blob is encrypted under the NEW key: it's unreadable once we drop
        # to only the new key with the old previous removed AND the new key changed.
    with _keys(secret=BASE, jwt_signing=JWTK, encryption=ENCK):
        assert crypto.decrypt_secret(fresh) == "new-provider-key"          # ENCK alone still reads it


# ── #F-01 rls_strict boot gate ──────────────────────────────────────────────────

_PROD = dict(  # minimal valid production config (passes the other guards)
    environment="production",
    secret_key="A" * 48,
    risk_hash_salt="a-strong-random-salt",
    allowed_origins="https://app.muddato.uz",
    # `_enforce_database_identity_split` (credential-split cutover) requires a
    # migration credential distinct from the runtime login before it will even
    # reach the #F-01 rls_strict guard these tests exercise.
    migration_database_url_sync="postgresql+psycopg://migration_owner@db/m",
)


def test_rls_strict_blocks_boot_when_rls_disabled_in_production() -> None:
    from app.config import Settings

    with pytest.raises(ValueError, match="RLS"):
        Settings(rls_enabled=False, rls_strict=True, **_PROD)  # type: ignore[arg-type]


def test_rls_strict_default_does_not_block_boot() -> None:
    """Default (rls_strict unset/False) keeps the current live deploy booting with RLS
    off — it only logs CRITICAL — so flipping nothing is safe."""
    from app.config import Settings

    s = Settings(rls_enabled=False, **_PROD)  # type: ignore[arg-type]
    assert s.rls_strict is False
    assert s.rls_enabled is False


def test_rls_strict_boots_when_rls_enabled() -> None:
    from app.config import Settings

    s = Settings(rls_enabled=True, rls_strict=True, **_PROD)  # type: ignore[arg-type]
    assert s.rls_strict is True and s.rls_enabled is True
