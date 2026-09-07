"""P1 Wave 1 hardening: JWT TTL/revocation, CSRF, CORS, logout (#740, #734, #746, #741, #843).

Mix of two tiers:

* Pure-logic / config checks (TTL value, CORS allowlist, cookie flag construction)
  run everywhere with no DB.
* HTTP behavioural checks (logout revocation, CSRF gate, Bearer exemption) run a
  real ASGI client backed by a real Postgres with a seeded user, via the
  ``p1_client`` fixture. They SKIP cleanly (never error) without a reachable test
  Postgres — the standard ``TEST_DATABASE_URL`` integration gate (set in CI;
  locally export ``TEST_DATABASE_URL``).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.core.security import TOKEN_TTL
from app.models import User

# A telegram id for the seeded P1 user.
_P1_TELEGRAM_ID = 770001


@pytest_asyncio.fixture
async def p1_client(migrated_db):
    """A real ASGI client backed by ``migrated_db`` with one seeded active user.

    Yields ``(client, db, user_token)`` where:
      * ``client`` — httpx AsyncClient bound to the app, with ``get_db`` overridden
        to a single shared session so a handler's commit (e.g. logout bumping
        ``tokens_valid_from``) is visible to the test's ``db`` immediately;
      * ``db`` — that same shared AsyncSession;
      * ``user_token`` — a Bearer token for the seeded user whose ``iat`` is a few
        seconds in the past, so a logout's revocation epoch (``now``) is strictly
        after it and the revoke check is deterministic (not a same-second race).

    Skips cleanly without a reachable test Postgres (``migrated_db`` handles the gate).
    """
    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    session = Session()

    async def _override_get_db():
        # Same session object for every request → the test's `db` sees committed writes.
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        user = User(telegram_id=_P1_TELEGRAM_ID, is_active=True)
        session.add(user)
        await session.commit()
        user_id = str(user.id)
        # iat 30s in the past → logout's tokens_valid_from (now) is strictly later.
        past = int((datetime.now(UTC) - timedelta(seconds=30)).timestamp())
        user_token = issue_token(user_id=user_id, extra={"iat": past})

        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client, session, user_token
    finally:
        app.dependency_overrides.pop(get_db, None)
        await session.close()


# ── (#734) JWT TTL — pure, runs everywhere ───────────────────────────────────

def test_token_ttl_is_one_day():
    """(#734) JWT TTL is 1 day, not 14 days."""
    assert timedelta(days=1) == TOKEN_TTL, (
        "JWT TTL must be 1 day per (#734) — was hardcoded to 14 days, "
        "which is too long. Sessions should auto-renew on activity."
    )


# ── (#746) session-cookie flags — pure construction check ─────────────────────

def test_session_cookie_has_samesite_strict():
    """(#746) The session cookie is set HttpOnly + SameSite=Strict (CSRF defence).

    Exercises the real ``set_session_cookie`` (the single code path every login
    uses) and asserts the actual Set-Cookie attributes, rather than a successful
    Telegram login that the test can't forge."""
    from starlette.responses import Response

    from app.core.security import SESSION_COOKIE_NAME, set_session_cookie

    resp = Response()
    set_session_cookie(resp, "a.b.c")
    set_cookie = resp.headers.get("set-cookie", "").lower()
    assert f"{SESSION_COOKIE_NAME.lower()}=" in set_cookie
    assert "httponly" in set_cookie, "session cookie must be HttpOnly (XSS-proof)"
    assert "samesite=strict" in set_cookie, "session cookie must be SameSite=Strict (#746)"


# ── (#734/#740) logout + revocation — DB-backed ──────────────────────────────

@pytest.mark.asyncio
async def test_logout_endpoint_exists(p1_client):
    """(#734) DELETE /api/auth/logout exists and returns 200 for an authed user."""
    client, _db, user_token = p1_client
    response = await client.delete(
        "/api/auth/logout", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 200, (
        f"logout endpoint returned {response.status_code}, expected 200"
    )


@pytest.mark.asyncio
async def test_logout_clears_session_cookie(p1_client):
    """(#734) Logout emits a Set-Cookie that deletes the session cookie."""
    client, _db, user_token = p1_client
    response = await client.delete(
        "/api/auth/logout", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 200
    from app.core.security import SESSION_COOKIE_NAME
    set_cookie = response.headers.get("set-cookie", "")
    # delete_cookie sets the cookie with an empty value + an expiry in the past.
    assert SESSION_COOKIE_NAME in set_cookie, "logout must Set-Cookie to clear the session"


@pytest.mark.asyncio
async def test_logout_revokes_all_tokens(p1_client):
    """(#740) Logout revokes outstanding tokens by bumping tokens_valid_from."""
    from sqlalchemy import select

    from app.core.security import decode_token

    client, db, user_token = p1_client
    user_id = decode_token(user_token).get("sub")

    user_before = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert (
        user_before.tokens_valid_from is None
        or user_before.tokens_valid_from < datetime.now(UTC)
    )

    response = await client.delete(
        "/api/auth/logout", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 200

    # Same shared session → the handler's commit is visible after a refresh.
    await db.refresh(user_before)
    assert user_before.tokens_valid_from is not None
    assert user_before.tokens_valid_from > datetime.now(UTC) - timedelta(seconds=30)


@pytest.mark.asyncio
async def test_jwt_revocation_via_tokens_valid_from(p1_client):
    """(#740) A token issued before tokens_valid_from is rejected after logout."""
    client, _db, user_token = p1_client

    # Works before logout.
    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 200, "valid token should work before logout"

    # Logout → revoke.
    response = await client.delete(
        "/api/auth/logout", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 200

    # The same (now pre-epoch) token is rejected.
    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 401, "revoked token should return 401"
    assert "token_revoked" in response.text or "invalid" in response.text.lower()


# ── (#746/#843) CSRF gate — DB-backed where the handler runs ─────────────────

@pytest.mark.asyncio
async def test_csrf_required_on_cookie_post(p1_client):
    """(#746/#843) A COOKIE-authenticated state change (the CSRF-vulnerable case)
    without X-CSRF-Token is blocked at the middleware with 403."""
    from app.core.security import SESSION_COOKIE_NAME

    client, _db, user_token = p1_client
    client.cookies.set(SESSION_COOKIE_NAME, user_token)  # ambient credential, no header
    response = await client.post("/api/auth/logout")
    assert response.status_code == 403, (
        "cookie POST without X-CSRF-Token should return 403. Got: "
        + str(response.status_code)
    )


@pytest.mark.asyncio
async def test_csrf_required_on_cookie_put(p1_client):
    """(#746/#843) A COOKIE-authenticated PUT without X-CSRF-Token is blocked at 403."""
    from app.core.security import SESSION_COOKIE_NAME

    client, _db, user_token = p1_client
    client.cookies.set(SESSION_COOKIE_NAME, user_token)
    response = await client.put("/api/auth/me", json={})
    assert response.status_code == 403, "cookie PUT without X-CSRF-Token should return 403"


@pytest.mark.asyncio
async def test_bearer_mutation_is_csrf_exempt(p1_client):
    """(#843) A Bearer (no-cookie) mutation can't be CSRF-forged, so it is EXEMPT —
    it must reach the handler (here: a real 200 logout) instead of a 403.
    (Requiring CSRF here would 403 every frontend mutation; the frontend is Bearer-only.)"""
    client, _db, user_token = p1_client
    response = await client.delete(
        "/api/auth/logout", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code != 403, (
        "a Bearer mutation must not be blocked by CSRF — got " + str(response.status_code)
    )
    assert response.status_code == 200, (
        "the exempt Bearer logout must reach the handler — got " + str(response.status_code)
    )


@pytest.mark.asyncio
async def test_csrf_exempts_webhooks(p1_client):
    """(#746) Webhook endpoints (signature-authed, not cookie) are CSRF-exempt.

    Posts to a REAL mounted webhook (``/api/webhooks/click/prepare``) with no body:
    it must be rejected for a missing/invalid payload (422), never with a 403 CSRF
    error — proving the ``/api/webhooks`` prefix is exempt from CSRF validation."""
    client, _db, _user_token = p1_client
    response = await client.post("/api/webhooks/click/prepare", data={})
    assert response.status_code != 403, (
        "webhooks must be CSRF-exempt — got 403 for /api/webhooks/click/prepare"
    )
    assert "csrf" not in response.text.lower()


@pytest.mark.asyncio
async def test_get_requests_exempt_from_csrf(p1_client):
    """(#746) GET requests never require a CSRF token (read-safe)."""
    client, _db, user_token = p1_client
    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 200, "GET should not require CSRF token"


# ── (#741) CORS allowlist — pure config, runs everywhere ──────────────────────

def test_cors_rejects_wildcard_origin_in_production():
    """(#741) CORS rejects a wildcard origin in production."""
    from app.config import settings

    if settings.is_production:
        assert "*" not in settings.allowed_origins, (
            "CORS wildcard '*' is not allowed in production per (#741)"
        )


def test_cors_whitelist_explicit_origins():
    """(#741) CORS allowlist uses explicit origins, not wildcards."""
    from app.config import settings

    origins = settings.allowed_origins_list
    assert isinstance(origins, list), "allowed_origins_list must be a list"
    assert len(origins) > 0, "at least one origin must be configured"
    for origin in origins:
        assert origin != "*", f"wildcard '*' not allowed per (#741), got: {origin}"
