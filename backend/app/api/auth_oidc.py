"""Telegram OIDC (OpenID Connect) login flow.

This is the **modern** auth path. The legacy iframe widget at
``oauth.telegram.org/auth?bot_id=…&embed=1`` shows a "deprecated" notice when
the bot's domain isn't registered with @BotFather — and even when it is, the
embed UX is awkward and Telegram is actively migrating users to OIDC.

OIDC requires the operator to register **Allowed URLs** in @BotFather:
  - Origin:       https://muddato.uz
  - Redirect URI: https://muddato.uz/auth/callback

Then Web Login → BotFather hands over a ``client_id`` + ``client_secret``
which we use here.

Flow:

  1. Browser: POST /auth/telegram/start          → { authorize_url, state }
                ↓ frontend `window.location.href = authorize_url`
  2. Telegram oauth.telegram.org/auth?…         → user authorizes
  3. Telegram → redirect_uri?code=…&state=…
  4. Browser: POST /auth/telegram/callback {code, state}
                → backend exchanges code at oauth.telegram.org/token
                → verifies id_token against Telegram's JWKS (RS256 public key)
                → upserts User, mints our JWT
                → returns { token }
  5. Browser: stores JWT, navigates to /app

Spec references:
  - https://core.telegram.org/widgets/login
  - https://core.telegram.org/bots/telegram-login
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from typing import Annotated
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from jwt import InvalidTokenError, PyJWK
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import _upsert_user
from app.config import settings
from app.core.ratelimit import login_limit
from app.core.redis import get_redis
from app.core.security import establish_session, issue_token
from app.db import get_db
from app.schemas.auth import LoginResponse

log = logging.getLogger("muddato.auth.oidc")
#: NO router-level authority — see the note on ``app.api.auth.router``.
#:
#: The OIDC callback resolves an account only AFTER ``_verify_id_token`` has
#: checked the id_token's signature against Telegram's JWKS and matched issuer,
#: audience, expiry and nonce. ``_upsert_user`` then takes the identity
#: authority for one statement, keyed on the verified ``sub``. An invalid token
#: raises before any of that, holding nothing.
router = APIRouter(prefix="/auth", tags=["auth"])

AUTHORIZE_URL = "https://oauth.telegram.org/auth"
TOKEN_URL     = "https://oauth.telegram.org/token"
JWKS_URL      = "https://oauth.telegram.org/.well-known/jwks.json"
ISSUER        = "https://oauth.telegram.org"
# `openid` alone returns only `sub`; `profile` is needed for name / username /
# picture (Telegram's claim set: name, preferred_username, picture). See the
# OIDC discovery doc: scopes_supported = [openid, phone, profile, telegram:bot_access].
SCOPES        = "openid profile"
STATE_TTL_SECONDS = 600  # 10 min — plenty for the user to authorize

# Telegram signs id_tokens with ASYMMETRIC keys (RS256 default, also ES256/EdDSA/
# ES256K) and publishes the public keys at the JWKS endpoint — NOT HS256 over the
# client_secret. We fetch + cache the JWKS and verify against it. TTL keeps the
# auth hot-path off the network on every login; on an unknown `kid` we refetch
# once (key rotation) and on a fetch error we serve the last-good set.
_JWKS_TTL_SECONDS = 3600.0
_jwks_cache: dict | None = None
_jwks_cached_at: float = 0.0

INT64_MAX = 2**63 - 1  # users.telegram_id is BIGINT — values above this overflow


def _telegram_id_from_claims(claims: dict) -> int:
    """Resolve the genuine Telegram user id from id_token claims.

    A real Telegram user id always fits in int64. Telegram's OIDC ``sub`` is
    sometimes an *opaque, per-client* subject identifier that exceeds int64 —
    feeding it straight into the BIGINT ``telegram_id`` column raises a DB
    DataError (the original 500). So we scan the common claim names and accept
    the first value that is a valid positive int64; otherwise we fail with a
    clean 4xx and log the full claim set for diagnosis.
    """
    for key in ("sub", "id", "user_id", "telegram_id", "uid"):
        raw = claims.get(key)
        if raw is None:
            continue
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 < val <= INT64_MAX:
            return val
    log.error("oidc.telegram_id_unresolved claims_keys=%s", list(claims.keys()))
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "telegram_id_unresolved")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE verifier + S256 challenge."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# ── id_token verification (JWKS) ──────────────────────────────────────────────

async def _get_jwks(*, force: bool = False) -> dict:
    """Return Telegram's JWKS, cached in-process for ``_JWKS_TTL_SECONDS``.

    On a fetch error we serve the last-good set if we have one (a transient
    network blip shouldn't break every login); only a cold cache + outage fails.
    """
    global _jwks_cache, _jwks_cached_at
    fresh = _jwks_cache is not None and (time.monotonic() - _jwks_cached_at) < _JWKS_TTL_SECONDS
    if not force and fresh:
        return _jwks_cache  # type: ignore[return-value]
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(JWKS_URL, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        log.warning("oidc.jwks_unreachable err=%s", type(e).__name__)
        if _jwks_cache is not None:
            return _jwks_cache  # serve stale rather than fail the login
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "jwks_unreachable") from e
    _jwks_cache = data
    _jwks_cached_at = time.monotonic()
    return data


def _select_jwk(jwks: dict, kid: str | None) -> dict | None:
    """Pick the signing key by ``kid``; fall back to the first RS256 key."""
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not keys:
        return None
    if kid:
        for k in keys:
            if k.get("kid") == kid:
                return k
        return None  # header named a kid we don't have → caller refetches once
    for k in keys:           # no kid in header (rare) → first RS256 key
        if k.get("alg") == "RS256":
            return k
    return keys[0]


async def _verify_id_token(id_token: str) -> dict:
    """Verify a Telegram OIDC id_token against the JWKS and return its claims.

    Raises ``InvalidTokenError`` on a bad signature / audience / issuer / expiry (the
    caller maps that to a clean 401), or ``HTTPException`` for an unresolvable
    signing key.
    """
    try:
        header = jwt.get_unverified_header(id_token)
    except InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "id_token_malformed") from e

    kid = header.get("kid")
    jwks = await _get_jwks()
    key = _select_jwk(jwks, kid)
    if key is None:
        jwks = await _get_jwks(force=True)  # key may have rotated
        key = _select_jwk(jwks, kid)
    if key is None:
        log.warning("oidc.jwks_kid_unknown kid=%s", kid)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "id_token_kid_unknown")

    alg = key.get("alg") or header.get("alg") or "RS256"
    # Construct the cryptographic key from Telegram's JWK and restrict decoding
    # to the algorithm advertised by that key. Any unsupported algorithm fails
    # closed as ``id_token_invalid`` at the caller.
    return jwt.decode(
        id_token,
        PyJWK.from_dict(key).key,
        algorithms=[alg],
        audience=settings.telegram_login_client_id,
        issuer=ISSUER,
        options={"verify_aud": True, "verify_iss": True, "verify_exp": True},
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

class StartResponse(BaseModel):
    authorize_url: str
    state: str


@router.post("/telegram/start", response_model=StartResponse, summary="OIDC: begin login",
             dependencies=[login_limit()])
async def telegram_start(response: Response) -> StartResponse:
    """Mint a one-shot OIDC state + PKCE pair, stash verifier in Redis, return
    the Telegram authorize URL for the frontend to redirect to.

    MUDD-25 H3: Bind state to an HttpOnly cookie for CSRF protection.
    """
    if not settings.telegram_login_client_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "client_id_unset")

    state = _b64url(secrets.token_bytes(24))
    nonce = _b64url(secrets.token_bytes(16))
    verifier, challenge = _pkce_pair()
    redirect_uri = f"{settings.frontend_url.rstrip('/')}/auth/callback"

    # Park the verifier server-side keyed by state. Telegram echoes back state
    # so we can recover the verifier without trusting the client.
    rds = get_redis()
    try:
        await rds.setex(
            f"oidc:state:{state}",
            STATE_TTL_SECONDS,
            f"{verifier}|{redirect_uri}|{nonce}",
        )
    except Exception as e:  # transient Upstash/Redis outage — fail clean, not 500
        log.warning("oidc.start_redis_unavailable err=%s", type(e).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth_store_unavailable") from e

    # MUDD-25 H3: Set HttpOnly cookie with state for CSRF protection
    response.set_cookie(
        key="muddato.oidc_state",
        value=state,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=True,
        # Lowercase: Starlette types this as Literal["lax", "strict", "none"].
        # "Lax" reaches the wire fine (the attribute is case-insensitive) but
        # does not type-check, and the two sibling cookies in
        # `app/core/security.py` already spell it lowercase.
        samesite="lax",
    )

    params = {
        "client_id":             settings.telegram_login_client_id,
        "redirect_uri":          redirect_uri,
        "response_type":         "code",
        "scope":                 SCOPES,
        "state":                 state,
        "nonce":                 nonce,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    return StartResponse(
        authorize_url=f"{AUTHORIZE_URL}?{urlencode(params)}",
        state=state,
    )


class CallbackRequest(BaseModel):
    code:  str
    state: str


@router.post("/telegram/callback", response_model=LoginResponse, summary="OIDC: finish login",
             dependencies=[login_limit()])
async def telegram_callback(
    payload: CallbackRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    """Exchange ``code`` for an id_token at Telegram's token endpoint, verify
    the JWT signature against Telegram's JWKS public keys, and mint our session JWT."""
    if not (settings.telegram_login_client_id and settings.telegram_login_client_secret):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "oidc_credentials_unset")

    rds = get_redis()
    
    # MUDD-25 H3: Verify state matches cookie value (CSRF protection)
    cookie_state = request.cookies.get("muddato.oidc_state")
    if not cookie_state or cookie_state != payload.state:
        # (MUDD-57 M-16) Never log either state value. This branch deliberately
        # does NOT delete the Redis entry, so the state it would print is still
        # live — printing it hands a log reader a usable CSRF token. Whether the
        # cookie was present at all is what actually distinguishes the two
        # failure modes (missing cookie vs. forged state).
        log.warning("oidc.callback_state_mismatch cookie_present=%s", bool(cookie_state))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "state_mismatch_or_missing")
    
    try:
        stashed = await rds.get(f"oidc:state:{payload.state}")
    except Exception as e:
        log.warning("oidc.callback_redis_unavailable err=%s", type(e).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth_store_unavailable") from e
    if not stashed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "state_expired_or_unknown")
    await rds.delete(f"oidc:state:{payload.state}")  # one-shot
    
    # Clear the CSRF cookie after verification
    response.delete_cookie("muddato.oidc_state", path="/")

    verifier, redirect_uri, expected_nonce = stashed.split("|", 2)

    # Token exchange — Basic auth = base64(client_id:client_secret), per RFC 6749.
    basic = base64.b64encode(
        f"{settings.telegram_login_client_id}:{settings.telegram_login_client_secret}".encode()
    ).decode()
    form = {
        "grant_type":    "authorization_code",
        "code":          payload.code,
        "redirect_uri":  redirect_uri,
        "code_verifier": verifier,
        # Identification alongside client_secret_basic (Telegram lists client_id
        # among the documented token-body params; harmless with Basic auth).
        "client_id":     settings.telegram_login_client_id,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.post(
                TOKEN_URL,
                data=form,
                headers={
                    "Authorization": f"Basic {basic}",
                    "Accept":        "application/json",
                },
            )
    except httpx.HTTPError as e:
        # (#147) Network failure reaching the IdP token endpoint → 502, not a 500.
        log.warning("oidc.token_exchange_unreachable err=%s", type(e).__name__)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "token_exchange_unreachable") from e
    if r.status_code >= 400:
        # (audit) Keep the upstream IdP error body in the SERVER log only — never
        # reflect it to the unauthenticated client (info disclosure).
        log.warning("oidc.token_exchange_failed status=%s", r.status_code)  # MUDD-25 M-16: redact response body to avoid logging tokens
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_exchange_failed")
    try:
        tokens = r.json()
    except Exception as e:
        log.warning("oidc.token_response_not_json content_type=%s", r.headers.get("content-type"))  # MUDD-25 M-16: redact response body
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_response_not_json") from e
    if not isinstance(tokens, dict):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_response_unexpected")
    log.info("oidc.token_response keys=%s", list(tokens.keys()))

    id_token = tokens.get("id_token")
    if not id_token:
        # OAuth2 error response: {"error": "...", "error_description": "..."}.
        # Telegram returns these on a failed exchange (often with HTTP 200, so the
        # >=400 branch above doesn't catch it). The `error`/`error_description`
        # fields are STANDARD, NON-SECRET diagnostics (invalid_grant, invalid_client,
        # …) — surface them so the failure is actionable instead of the opaque
        # "telegram returned ['error']". (No token is present in an error body, so
        # the #144 secret-leak concern doesn't apply here.)
        if "error" in tokens:
            err  = str(tokens.get("error"))
            desc = str(tokens.get("error_description") or "").strip()
            log.warning("oidc.token_error error=%s desc=%s", err, desc[:300])
            detail = f"telegram_oauth_error: {err}"
            if desc:
                detail += f" — {desc[:200]}"
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail)
        # Fallback: Telegram's OIDC sometimes returns the user data INSIDE the
        # `access_token` as a JWT (or as a userinfo object directly in the body).
        # Try those paths before giving up.
        candidate = tokens.get("access_token")
        if candidate and isinstance(candidate, str) and candidate.count(".") == 2:
            id_token = candidate
            log.info("oidc.using_access_token_as_id_token")
        else:
            # (#125) FAIL CLOSED: no id_token and no JWT access_token. The old
            # userinfo-body fallback minted a session from UNSIGNED, nonce-unchecked
            # claims — a forge-identity hole if the token-endpoint response is
            # influenced (MITM/spoofed). A real Telegram OIDC response carries a
            # verifiable id_token; anything else is rejected.
            # (#144) Log only the response KEYS — str(tokens) leaks access_token/id_token.
            log.warning("oidc.id_token_missing keys=%s", list(tokens.keys()))
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                f"id_token_missing: telegram returned {list(tokens.keys())}",
            )

    # Verify + decode the id_token against Telegram's JWKS (RS256 default). Telegram
    # signs with ASYMMETRIC keys verified by PUBLIC key — NOT HS256 over the
    # client_secret. Issuer + audience + expiry are all checked inside _verify_id_token.
    try:
        claims = await _verify_id_token(id_token)
    except HTTPException:
        raise  # unresolvable signing key (kid/jwks) — already a clean 401/502
    except InvalidTokenError as e:
        # FAIL CLOSED (#66). Never fall back to unverified claims — accepting a
        # JWT whose signature/audience/issuer we could not verify is a textbook
        # auth bypass: if the token-endpoint response is influenced (MITM, spoofed
        # endpoint, misconfig) an attacker forges identity. A legitimately issued
        # Telegram id_token verifies here; anything else is rejected outright.
        log.warning("oidc.id_token_verify_failed err=%s", type(e).__name__)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "id_token_invalid") from e

    # Reject (don't just log) a present-but-wrong nonce — it means this id_token
    # was not minted for this browser session (replay/confusion). A *missing*
    # nonce is still tolerated (some Telegram installs drop it; code+state+PKCE
    # already bind the session), but a *mismatched* one is rejected.
    if claims.get("nonce") and claims["nonce"] != expected_nonce:
        log.warning("oidc.nonce_mismatch — rejecting")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "nonce_mismatch")

    log.info("oidc.claims keys=%s", list(claims.keys()))
    tg_id = _telegram_id_from_claims(claims)

    user = await _upsert_user(
        db,
        telegram_id=tg_id,
        username=claims.get("preferred_username"),
        first_name=claims.get("given_name") or claims.get("name"),
        last_name=claims.get("family_name"),
        photo_url=claims.get("picture"),
        language_code=claims.get("locale"),
    )
    token = issue_token(user_id=str(user.id))
    # (#257) Set the HttpOnly session cookie + readable double-submit CSRF cookie so the
    # browser is authenticated by the cookie (token still returned in-body for SDK clients).
    establish_session(response, token)
    return LoginResponse(token=token)
