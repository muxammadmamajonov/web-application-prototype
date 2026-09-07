"""Legacy OIDC-token-in-`hash` verification (auth.py).

The `/auth/telegram/oauth` and `/signup` endpoints accept an OIDC id_token in the
`hash` field (old iframe-widget fallback). They used to decode it with HS256 over
the client_secret — a contract Telegram never implemented (it signs RS256 via JWKS).
These tests pin the corrected behaviour: the helpers delegate to the canonical
JWKS verifier and map the real Telegram claims.

Pure-unit: an injected JWKS, no network / Redis / DB.
"""
from __future__ import annotations

import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.api import auth as auth_mod
from app.api import auth_oidc
from app.api.auth import OAuthCallbackRequest
from app.config import settings

KID = "oidc-1"
AUD = "test-client-id"


def _uint_b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _rsa_keypair() -> tuple[str, dict]:
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = priv.public_key().public_numbers()
    jwk = {
        "kty": "RSA", "alg": "RS256", "use": "sig", "kid": KID,
        "n": _uint_b64(nums.n), "e": _uint_b64(nums.e),
    }
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return pem, jwk


def _sign(pem: str, claims: dict) -> str:
    now = int(time.time())
    body = {"iss": auth_oidc.ISSUER, "aud": AUD, "iat": now, "exp": now + 600, **claims}
    return jwt.encode(body, pem, algorithm="RS256", headers={"kid": KID})


def _payload(raw_hash: str) -> OAuthCallbackRequest:
    return OAuthCallbackRequest(id=1, auth_date=int(time.time()), hash=raw_hash)


@pytest.fixture(autouse=True)
def _client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_login_client_id", AUD, raising=False)


@pytest.fixture
def signing(monkeypatch: pytest.MonkeyPatch) -> str:
    pem, pub = _rsa_keypair()

    async def _fake_get_jwks(*, force: bool = False) -> dict:
        return {"keys": [pub]}

    monkeypatch.setattr(auth_oidc, "_get_jwks", _fake_get_jwks)
    return pem


@pytest.mark.asyncio
async def test_hash_jwt_verifies_and_maps_claims(signing: str) -> None:
    """A genuine RS256 id_token in `hash` verifies and overwrites payload identity."""
    token = _sign(signing, {
        "sub": "777000123", "name": "Aziz",
        "preferred_username": "aziz", "picture": "https://t.me/i/x.jpg",
        "phone_number": "+998901234567",
    })
    payload = _payload(token)

    claims = await auth_mod._oidc_claims_from_hash(token)
    auth_mod._apply_oidc_claims(payload, claims)

    assert payload.id == 777000123
    assert payload.first_name == "Aziz"
    assert payload.username == "aziz"
    assert payload.photo_url == "https://t.me/i/x.jpg"
    assert payload.phone_number == "+998901234567"


@pytest.mark.asyncio
async def test_hash_jwt_forged_rejected(signing: str) -> None:
    """A token signed by a different key → 401 telegram_oidc_invalid (fail closed)."""
    other_pem, _ = _rsa_keypair()
    forged = _sign(other_pem, {"sub": "1"})
    with pytest.raises(HTTPException) as ei:
        await auth_mod._oidc_claims_from_hash(forged)
    assert ei.value.status_code == 401
    assert ei.value.detail == "telegram_oidc_invalid"


@pytest.mark.asyncio
async def test_hash_jwt_client_id_unset_503(signing: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """No client_id configured → clean 503, not a 500."""
    monkeypatch.setattr(settings, "telegram_login_client_id", None, raising=False)
    token = _sign(signing, {"sub": "1"})
    with pytest.raises(HTTPException) as ei:
        await auth_mod._oidc_claims_from_hash(token)
    assert ei.value.status_code == 503
    assert ei.value.detail == "oidc_client_id_unset"


def test_auth_module_no_hs256() -> None:
    """Regression: the broken HS256-over-client_secret scheme must be gone from auth.py."""
    import inspect
    src = inspect.getsource(auth_mod)
    assert 'algorithms=["HS256"]' not in src
