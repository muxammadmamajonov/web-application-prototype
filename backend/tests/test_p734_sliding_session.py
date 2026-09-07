"""#734 — JWT sliding session: an active *cookie* session renews on the fly once
it is past half its TTL, so a working user isn't logged out at the 1-day mark,
while an idle session still expires. Bearer/SDK clients don't slide; a revoked
session can't renew itself (renewal runs only past the revocation-epoch check).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import User

# ── config / decision logic (DB-free) ────────────────────────────────────────

def test_ttl_is_one_day_and_renew_after_half() -> None:
    from app.core.security import _RENEW_AFTER, TOKEN_TTL

    assert timedelta(days=1) == TOKEN_TTL
    assert _RENEW_AFTER == TOKEN_TTL / 2


def test_maybe_renew_session_logic(monkeypatch) -> None:
    from app.config import settings
    from app.core.security import _RENEW_AFTER, _maybe_renew_session, decode_token

    # PyJWT correctly warns on weak HMAC test keys. Use a production-strength
    # fixture so the test output stays actionable rather than normalising that warning.
    monkeypatch.setattr(settings, "secret_key", "test-session-key-32-bytes-minimum!!")
    monkeypatch.setattr(settings, "jwt_signing_key", None)

    now = int(datetime.now(UTC).timestamp())
    half = int(_RENEW_AFTER.total_seconds())

    # plain session past half-life → renews, with a *fresh* iat for the same sub
    fresh = _maybe_renew_session({"sub": "u1", "iat": now - half - 60, "exp": now + 100}, "u1")
    assert fresh is not None
    decoded = decode_token(fresh)
    assert decoded["sub"] == "u1"
    assert decoded["iat"] >= now - 1  # brand-new iat, not the stale one

    # young session → no renewal
    assert _maybe_renew_session({"sub": "u1", "iat": now - 10, "exp": now + 100}, "u1") is None
    # exactly at the threshold renews; just under does not
    assert _maybe_renew_session({"sub": "u1", "iat": now - half, "exp": now + 100}, "u1") is not None
    assert _maybe_renew_session({"sub": "u1", "iat": now - half + 30, "exp": now + 100}, "u1") is None

    # a token carrying ANY extra claim expires naturally, never auto-renews
    assert _maybe_renew_session({"sub": "u1", "iat": now - half - 60, "exp": now + 100, "imp": "x"}, "u1") is None
    # missing / non-int iat → no renewal (defensive)
    assert _maybe_renew_session({"sub": "u1", "exp": now + 100}, "u1") is None
    assert _maybe_renew_session({"sub": "u1", "iat": "nope", "exp": now + 100}, "u1") is None


# ── wiring through current_user (integration — needs the test DB) ─────────────

def _cookie_token(user_id: str, age_seconds: int) -> str:
    import jwt

    from app.config import settings
    from app.core.security import ALGORITHM, TOKEN_TTL

    now = int(datetime.now(UTC).timestamp())
    return jwt.encode(
        {"sub": user_id, "iat": now - age_seconds, "exp": now + int(TOKEN_TTL.total_seconds())},
        settings.secret_key, algorithm=ALGORITHM,
    )


async def test_current_user_slides_old_cookie_but_not_fresh_or_bearer(migrated_db) -> None:
    from app.core.security import _RENEW_AFTER, SESSION_COOKIE_NAME, current_user, issue_token

    over_half = int(_RENEW_AFTER.total_seconds()) + 60
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        u = User(telegram_id=970734, is_active=True)
        s.add(u)
        await s.flush()
        old = _cookie_token(str(u.id), over_half)

        # cookie session past half-life → renewed cookie is set on the response
        resp = Response()
        got = await current_user(response=resp, token=old, db=s, cookie_token=old)
        assert got.id == u.id
        assert SESSION_COOKIE_NAME in resp.headers.get("set-cookie", ""), "old cookie session must slide"

        # a fresh cookie session → no renewal
        fresh = issue_token(user_id=str(u.id))
        resp2 = Response()
        await current_user(response=resp2, token=fresh, db=s, cookie_token=fresh)
        assert "set-cookie" not in resp2.headers, "fresh session must not slide"

        # Bearer (no cookie) even if old → no renewal
        resp3 = Response()
        await current_user(response=resp3, token=old, db=s, cookie_token=None)
        assert "set-cookie" not in resp3.headers, "bearer client must not get a session cookie"


async def test_revoked_session_does_not_renew(migrated_db) -> None:
    """A session past half-life but revoked is rejected (401), never renewed."""
    import pytest
    from fastapi import HTTPException

    from app.core.security import _RENEW_AFTER, current_user

    over_half = int(_RENEW_AFTER.total_seconds()) + 60
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        u = User(telegram_id=970735, is_active=True)
        u.tokens_valid_from = datetime.now(UTC)  # revoke everything issued before now
        s.add(u)
        await s.flush()
        old = _cookie_token(str(u.id), over_half)
        resp = Response()
        with pytest.raises(HTTPException) as ei:
            await current_user(response=resp, token=old, db=s, cookie_token=old)
        assert ei.value.status_code == 401
        assert "set-cookie" not in resp.headers, "a revoked session must never be renewed"
