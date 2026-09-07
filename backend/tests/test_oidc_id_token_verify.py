"""OIDC id_token verification against Telegram's JWKS.

Pins the *correct* contract: Telegram signs id_tokens with ASYMMETRIC keys
(RS256 default) verified via the public JWKS — NOT HS256 over the client_secret.
Before the fix the callback decoded with ``algorithms=["HS256"]`` and the
client_secret as the key, which can never validate a real Telegram token.

These are pure-unit tests of ``_verify_id_token`` with an injected JWKS — no
network, no Redis, no DB.
"""
from __future__ import annotations

import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.api import auth_oidc
from app.config import settings

KID = "oidc-1"
AUD = "test-client-id"


def _uint_b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _rsa_keypair() -> tuple[str, dict]:
    """Return (private_pem, public_jwk) for an RS256 signing key."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = priv.public_key().public_numbers()
    jwk = {
        "kty": "RSA", "alg": "RS256", "use": "sig", "kid": KID,
        "n": _uint_b64(nums.n),
        "e": _uint_b64(nums.e),
    }
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return pem, jwk


def _sign(pem: str, claims: dict, *, kid: str = KID) -> str:
    now = int(time.time())
    body = {"iss": auth_oidc.ISSUER, "aud": AUD, "iat": now, "exp": now + 600, **claims}
    return jwt.encode(body, pem, algorithm="RS256", headers={"kid": kid})


@pytest.fixture(autouse=True)
def _client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_login_client_id", AUD, raising=False)


@pytest.fixture
def jwks(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Inject a test JWKS so ``_verify_id_token`` never hits the network."""
    pem, pub = _rsa_keypair()
    keyset = {"keys": [pub]}

    async def _fake_get_jwks(*, force: bool = False) -> dict:
        return keyset

    monkeypatch.setattr(auth_oidc, "_get_jwks", _fake_get_jwks)
    return {"pem": pem, "keyset": keyset}


@pytest.mark.asyncio
async def test_valid_rs256_token_verifies(jwks: dict) -> None:
    """A genuine RS256 id_token decodes to its claims."""
    token = _sign(jwks["pem"], {"sub": "777000123", "name": "Aziz", "preferred_username": "aziz"})
    claims = await auth_oidc._verify_id_token(token)
    assert claims["sub"] == "777000123"
    assert claims["preferred_username"] == "aziz"


@pytest.mark.asyncio
async def test_forged_signature_rejected(jwks: dict) -> None:
    """A token signed by a DIFFERENT key (same kid) is rejected — fail closed."""
    other_pem, _ = _rsa_keypair()
    forged = _sign(other_pem, {"sub": "1"})
    with pytest.raises(Exception):  # noqa: B017 — InvalidTokenError bubbles to the caller
        await auth_oidc._verify_id_token(forged)


@pytest.mark.asyncio
async def test_wrong_audience_rejected(jwks: dict) -> None:
    """An id_token minted for a different client_id is rejected."""
    now = int(time.time())
    token = jwt.encode(
        {"iss": auth_oidc.ISSUER, "aud": "someone-else", "sub": "1",
         "iat": now, "exp": now + 600},
        jwks["pem"], algorithm="RS256", headers={"kid": KID},
    )
    with pytest.raises(Exception):  # noqa: B017
        await auth_oidc._verify_id_token(token)


@pytest.mark.asyncio
async def test_unknown_kid_raises_clean_401(jwks: dict) -> None:
    """A token whose kid isn't in the JWKS yields a clean 401, not a 500."""
    token = _sign(jwks["pem"], {"sub": "1"}, kid="rotated-away")
    with pytest.raises(HTTPException) as ei:
        await auth_oidc._verify_id_token(token)
    assert ei.value.status_code == 401
    assert ei.value.detail == "id_token_kid_unknown"


def test_hs256_no_longer_accepted() -> None:
    """Regression: the old HS256-over-client_secret scheme must be gone."""
    import inspect
    src = inspect.getsource(auth_oidc)
    assert 'algorithms=["HS256"]' not in src
    assert "JWKS" in src
