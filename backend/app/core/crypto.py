"""Symmetric encryption for secrets stored at rest (payment provider keys).

The key is derived from ``ENCRYPTION_KEY`` when set, else from ``SECRET_KEY``
(#F-21 key separation — unset falls back so we don't introduce a hard new managed
secret and existing payloads stay decryptable). Stored values are prefixed with
``enc:`` so reads can transparently handle a mix of encrypted and legacy-plaintext
rows during/after migration:

* ``encrypt_secret`` → ``"enc:" + <fernet token>``
* ``decrypt_secret`` → plaintext for an ``enc:`` value; the value unchanged for
  a legacy plaintext value; ``None`` if an ``enc:`` token fails to decrypt
  (e.g. SECRET_KEY changed) — callers treat that as "no secret", which fails
  closed rather than signing with garbage.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.config import settings

log = logging.getLogger("muddato.crypto")

_PREFIX = "enc:"


def _derive(secret: str) -> bytes:
    """Derive a urlsafe-base64 Fernet key from a SECRET_KEY string."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _encryption_secret() -> str:
    """The secret the Fernet key is derived from (#F-21).

    Falls back to ``secret_key`` when ``encryption_key`` is unset, so payloads
    encrypted in the single-key era still decrypt and behaviour is byte-identical
    when the new env var is absent. Set ``ENCRYPTION_KEY`` in prod so a leak of the
    JWT signing key can't decrypt payment secrets.
    """
    return settings.encryption_key or settings.secret_key


@lru_cache(maxsize=1)
def _multifernet() -> MultiFernet:
    """MultiFernet over the current encryption secret (primary — used to encrypt)
    plus any SECRET_KEY_PREVIOUS values (decrypt-only), enabling zero-downtime key
    rotation (#133): set the old key in SECRET_KEY_PREVIOUS and the new one in
    ENCRYPTION_KEY (or SECRET_KEY pre-split), deploy, then re-encrypt at leisure with
    ``rotate_secret`` before retiring the old key. Cached per process; a deploy with
    rotated config builds a fresh one.

    The primary is ``encryption_key or secret_key`` (#F-21): when ENCRYPTION_KEY is
    unset this is exactly ``_derive(secret_key)`` as before, so existing ``enc:``
    payloads remain decryptable. SECRET_KEY_PREVIOUS still carries the prior
    encryption key(s) for decrypt-only — including the old SECRET_KEY itself, which
    is how you migrate from the single-key era to a distinct ENCRYPTION_KEY without
    downtime.
    """
    keys = [Fernet(_derive(_encryption_secret()))]
    prev = settings.secret_key_previous or ""
    keys += [Fernet(_derive(k.strip())) for k in prev.split(",") if k.strip()]
    return MultiFernet(keys)


def encrypt_secret(plaintext: str) -> str:
    return _PREFIX + _multifernet().encrypt(plaintext.encode()).decode()


def is_encrypted(stored: str | None) -> bool:
    return stored is not None and stored.startswith(_PREFIX)


def decrypt_secret(stored: str | None) -> str | None:
    if not stored:
        return stored
    if not stored.startswith(_PREFIX):
        return stored  # legacy plaintext — passthrough
    try:
        return _multifernet().decrypt(stored[len(_PREFIX):].encode()).decode()
    except (InvalidToken, ValueError):
        log.error("crypto.decrypt_failed — secret unreadable (SECRET_KEY rotated?)")
        return None


def rotate_secret(stored: str | None) -> str | None:
    """Re-encrypt an ``enc:`` value to the CURRENT primary key (#133).

    For a one-off re-encryption pass after a rotation, so the old key in
    SECRET_KEY_PREVIOUS can eventually be retired. Legacy plaintext and ``None``
    pass through; an unreadable token returns ``None`` (fail-closed).
    """
    if not stored or not stored.startswith(_PREFIX):
        return stored
    try:
        return _PREFIX + _multifernet().rotate(stored[len(_PREFIX):].encode()).decode()
    except (InvalidToken, ValueError):
        log.error("crypto.rotate_failed — secret unreadable")
        return None


def blind_index(value: str, *, context: str) -> str:
    """Deterministic, keyed hash for an indexed equality lookup over a value kept
    encrypted at rest (#84). Keyed by SECRET_KEY and domain-separated by
    ``context`` (the same value in two columns yields different indexes), it lets
    a webhook find the one matching row in O(1) without decrypting every row —
    while revealing nothing to anyone who doesn't hold SECRET_KEY."""
    key = hashlib.sha256(f"{settings.secret_key}|bidx|{context}".encode()).digest()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


class EncryptedStr(TypeDecorator):
    """A String column transparently Fernet-encrypted at rest (#72).

    Python sees plaintext; Postgres stores ``enc:<token>``. Reads pass legacy
    plaintext through unchanged (so rows written before this type still read,
    and the one-time backfill can run lazily), and a token that fails to decrypt
    comes back as ``None`` (fail-closed) rather than leaking ciphertext into a
    response. ``EncryptedStr(n)`` backs onto ``String(n)`` (default 255 — a
    Fernet token for a short identifier is ~120 chars); pick a width that fits
    the ciphertext of the longest plaintext stored (e.g. 500 for a bot token).
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int = 255, **kw: object) -> None:
        super().__init__(length=length, **kw)

    def process_bind_param(self, value: str | None, dialect: object) -> str | None:
        return None if value is None else encrypt_secret(str(value))

    def process_result_value(self, value: str | None, dialect: object) -> str | None:
        return None if value is None else decrypt_secret(value)
