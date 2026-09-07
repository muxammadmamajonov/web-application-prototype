"""JWT session tokens — short-lived bearer issued after Telegram login.

(#P1-A) Tokens are now issued in both:
1. HttpOnly cookie (muddato.session) for browser clients — XSS-proof
2. Response body (LoginResponse.token) for non-browser clients (SDKs, CLIs)

Extraction is cookie-first: browser sends cookie automatically; SDKs use bearer token.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import Cookie, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db, set_identity_scope
from app.models import User

ALGORITHM = "HS256"
# (#734) 1-day TTL. An *active* cookie session is renewed on the fly once it passes
# half its life (see _maybe_renew_session + current_user), so a working user is never
# logged out mid-day; an *idle* session still expires at 24h.
TOKEN_TTL = timedelta(days=1)
# Renew a cookie session once it is older than this (half the TTL).
_RENEW_AFTER = TOKEN_TTL / 2
SESSION_COOKIE_NAME = "muddato.session"
# (#257) Readable double-submit CSRF token cookie. Deliberately NOT HttpOnly — the SPA
# must read it to echo it back in the X-CSRF-Token header; the security comes from the
# same-origin policy (a cross-site attacker can neither read it nor set the header).
CSRF_COOKIE_NAME = "muddato.csrf"

bearer = OAuth2PasswordBearer(tokenUrl="/api/auth/telegram", auto_error=False)


def _jwt_key() -> str:
    """The HS256 key for session JWTs (#F-21).

    Falls back to ``secret_key`` when ``jwt_signing_key`` is unset, so a token
    signed today (single-key era) still verifies and behaviour is byte-identical
    when the new env var is absent. Set ``JWT_SIGNING_KEY`` in prod so a leak of
    the encryption key can't forge sessions. Resolved per-call (not cached) so a
    test or rotation that swaps the setting takes effect immediately.

    Note: ``sign_receipt`` / ``sign_media`` / ``blind_index`` deliberately keep
    using ``secret_key`` — they are capability/blind-index HMACs whose existing
    outputs must stay stable, and are out of the JWT/encryption split's scope.
    """
    return settings.jwt_signing_key or settings.secret_key


def sign_receipt(blob_id: str) -> str:
    """Stable, unguessable capability token for a receipt blob.

    The receipt-serve endpoint is rendered directly via ``<img src>`` / ``<a
    href>``, which can't carry the bearer token, so access is gated by this
    HMAC instead. No expiry: the URL is stored once and only ever handed out
    inside authenticated API responses.
    """
    return hmac.new(
        settings.secret_key.encode(), f"receipt:{blob_id}".encode(), hashlib.sha256,
    ).hexdigest()[:32]


def verify_receipt_sig(blob_id: str, sig: str | None) -> bool:
    return bool(sig) and sig is not None and hmac.compare_digest(sign_receipt(blob_id), sig)


def sign_media(media_id: str) -> str:
    """Capability token for a Telegram-stored media file (#814).

    Mirrors :func:`sign_receipt` but in a SEPARATE keyspace (``media:`` prefix), so
    a receipt signature can never authorize a media id and vice-versa. The
    ``/api/media`` proxy is rendered via ``<img src>`` / ``<a href>``, which can't
    carry the bearer token, so access is gated by this HMAC instead.
    """
    return hmac.new(
        settings.secret_key.encode(), f"media:{media_id}".encode(), hashlib.sha256,
    ).hexdigest()[:32]


def verify_media_sig(media_id: str, sig: str | None) -> bool:
    return bool(sig) and sig is not None and hmac.compare_digest(sign_media(media_id), sig)


def sign_download(kind: str, ident: str, expires_at: int) -> str:
    """Short-lived capability token for a generated download (#10).

    A THIRD keyspace (``dl:``), separate from ``receipt:`` and ``media:``, so a
    signature minted for one class of object can never authorise another. The
    expiry is inside the signed payload, not merely a query parameter, so it
    cannot be edited by whoever holds the link.

    Why this exists at all: contract PDFs, invoices and report exports are
    generated on demand behind a bearer token, which a Telegram WebView cannot
    attach to a plain link — so every one of those downloads had to be fetched
    into a ``blob:`` URL, and Telegram refuses to hand a ``blob:`` origin to the
    system browser. The download silently did nothing. A signed HTTPS URL is a
    link the WebView *can* open.

    Unlike the two older helpers this one EXPIRES, because these URLs are handed
    out per click rather than stored once: a link that grants a contract PDF
    forever is a liability, and nothing needs it to live longer than the moment
    between the click and the browser opening.
    """
    payload = f"dl:{kind}:{ident}:{expires_at}"
    return hmac.new(
        settings.secret_key.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()[:32]


def verify_download_sig(kind: str, ident: str, expires_at: int, sig: str | None) -> bool:
    """Constant-time signature check AND expiry check.

    Both, deliberately: verifying the signature alone would accept a valid
    signature over an elapsed deadline, which is the whole point of signing the
    deadline. The signature is checked first so an expired-but-forged token and
    an expired-but-genuine one are indistinguishable to the caller.
    """
    if not sig:
        return False
    if not hmac.compare_digest(sign_download(kind, ident, expires_at), sig):
        return False
    return expires_at > int(datetime.now(UTC).timestamp())


def issue_token(*, user_id: str, extra: dict | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + TOKEN_TTL).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, _jwt_key(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _jwt_key(), algorithms=[ALGORITHM])
    except InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token") from e


def set_session_cookie(response: Response, token: str) -> None:
    """Set an HttpOnly session cookie with OWASP-recommended settings.
    
    (#P1-A) HttpOnly prevents XSS from reading the token; Secure ensures
    HTTPS-only transmission in production; SameSite=Strict blocks CSRF.
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(TOKEN_TTL.total_seconds()),
        secure=settings.is_production,  # HTTPS-only in production
        httponly=True,  # XSS-proof: JS cannot read this cookie
        samesite="strict",  # CSRF protection
        domain=None,  # Let the browser infer from request
        path="/",
    )


def issue_csrf_token() -> str:
    """A random, unguessable double-submit CSRF token (stateless — no server storage)."""
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, csrf_token: str) -> None:
    """Set the readable double-submit CSRF cookie (#257).

    NOT HttpOnly on purpose: the SPA reads it and echoes it in the ``X-CSRF-Token``
    header so the middleware can verify header == cookie. A cross-site attacker can
    neither read this cookie (same-origin policy) nor set the custom header, so the
    pair cannot be forged. Mirrors the session cookie's Secure/SameSite/expiry.
    """
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=int(TOKEN_TTL.total_seconds()),
        secure=settings.is_production,
        httponly=False,  # MUST be JS-readable for the double-submit pattern
        samesite="strict",
        domain=None,
        path="/",
    )


def establish_session(response: Response, token: str) -> None:
    """Set BOTH the HttpOnly session cookie and a fresh double-submit CSRF cookie.

    Every login path must call this (not just ``set_session_cookie``) so a
    cookie-authenticated browser also receives the CSRF token it needs to make
    state-changing requests. (#257)
    """
    set_session_cookie(response, token)
    set_csrf_cookie(response, issue_csrf_token())


def _maybe_renew_session(payload: dict[str, Any], user_id: str) -> str | None:
    """(#734) Decide whether a *cookie* session is due for a sliding renewal.

    Returns a fresh token to set on the cookie, or ``None`` to leave it as-is. Renews
    only a plain user session (no extra claims) once it is past half its TTL — so an
    active user isn't logged out at the 1-day mark while an idle one still expires.
    The caller invokes this only AFTER the revocation-epoch check, so a revoked
    session can't renew itself, and the fresh token carries a new ``iat`` so renewal
    never slips a session past a future revocation."""
    if set(payload) - {"sub", "iat", "exp"}:
        return None  # tokens carrying extra claims expire naturally, never auto-renew
    iat = payload.get("iat")
    if not isinstance(iat, int):
        return None
    if datetime.now(UTC).timestamp() - iat < _RENEW_AFTER.total_seconds():
        return None
    return issue_token(user_id=user_id)


async def extract_token_from_request(
    cookie_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    bearer_token: Annotated[str | None, Depends(bearer)] = None,
) -> str | None:
    """Extract JWT from cookie (primary) or bearer token (fallback).
    
    Browser clients send the cookie automatically; non-browser clients
    (SDKs, CLIs, scripts) can use the Authorization: Bearer header.
    """
    # Cookie takes precedence — it's how browsers send the token
    if cookie_token:
        return cookie_token
    # Fallback to bearer token for non-browser clients
    if bearer_token:
        return bearer_token
    # No token found
    return None


def assert_token_current(payload: dict[str, Any], user: User) -> None:
    """Raise 401 unless ``payload`` is still a LIVE session for ``user``.

    A valid signature only proves the token was minted by us; it says nothing
    about whether the account still exists as an employee or whether the session
    was logged out. Those two facts live in the database, so every caller that
    turns a token into an authorization decision has to ask:

      * ``is_active`` — a deactivated (fired) staff account, and
      * ``tokens_valid_from`` (#123) — the revocation epoch that "log out
        everywhere" / a leaked-token reset bumps, invalidating every outstanding
        token before its normal expiry.

    (MUDD-57 M-15) Factored out of ``current_user`` because ``catalog._optional_tenant_id``
    grew a second, weaker authenticator: it called ``decode_token`` alone, so a
    logged-out or deactivated user kept scoping catalog reads to their tenant and
    kept seeing that tenant's *proposed* models. A best-effort caller catches this
    ``HTTPException`` and degrades to anonymous; the point is that the conditions
    are defined once and cannot drift apart again.
    """
    import logging
    log = logging.getLogger("muddato.auth")

    if not user.is_active:
        log.warning("auth.401 user_inactive user_id=%s", user.id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user_inactive")
    tvf = user.tokens_valid_from
    if tvf is not None:
        iat = payload.get("iat")
        if not isinstance(iat, int) or iat < int(tvf.timestamp()):
            log.warning("auth.401 token_revoked user_id=%s", user.id)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_revoked")


async def current_user(
    response: Response,
    token: Annotated[str | None, Depends(extract_token_from_request)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cookie_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE_NAME)] = None,
) -> User:
    import logging
    log = logging.getLogger("muddato.auth")

    if not token:
        log.warning("auth.401 missing_token")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing_token")
    try:
        payload = decode_token(token)
    except HTTPException:
        # (#70) Never log token material — no prefix, no claims.
        log.warning("auth.401 invalid_token")
        raise
    user_id = payload.get("sub")
    if not user_id:
        log.warning("auth.401 bad_token")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_token")
    # (Batch 5) Pin the DB identity from the token BEFORE the account lookup.
    # The signature is what proves who this is; the SELECT only confirms the
    # account is still live. Under the fail-closed `users` policy an unscoped
    # session sees no user at all, so without this the lookup below would find
    # nothing and every request would 401 — and, more to the point, a request
    # that never resolves a tenant now reads exactly one user row: its own.
    await set_identity_scope(db, user_id)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        log.warning("auth.401 user_not_found user_id=%s", user_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user_not_found")
    assert_token_current(payload, user)
    # (#734) Sliding session: renew an active *cookie* session that's past half its
    # TTL. Bearer/SDK clients manage their own tokens, so only the cookie path slides.
    # (An endpoint returning its own Response object sets the renewed cookie on the
    # next dict/model response instead — benign for a frequently-polling client.)
    if cookie_token and cookie_token == token:
        fresh = _maybe_renew_session(payload, str(user.id))
        if fresh is not None:
            set_session_cookie(response, fresh)
            # (#257) Keep the double-submit CSRF cookie alive alongside the renewed
            # session — re-set the SAME value (no in-flight token rotation), just
            # refreshing its expiry so it never lapses while the session slides.
            set_csrf_cookie(response, csrf_cookie or issue_csrf_token())
    return user
