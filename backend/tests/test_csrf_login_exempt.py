"""(#843-followup) CSRF middleware must not block the things it was over-blocking:

* a returning user whose browser still holds a session cookie must be able to (re-)login —
  the login endpoints are authenticated by the Telegram hash, not the cookie;
* a Bearer-authenticated request must not be CSRF-blocked just because the browser also sent
  a now-vestigial session cookie alongside the Authorization header.

A cookie-ONLY state change (no Bearer) still requires the X-CSRF-Token — no regression.
"""
from __future__ import annotations


async def test_login_with_stale_cookie_is_not_csrf_blocked(async_client):
    from app.core.security import SESSION_COOKIE_NAME

    # The exact failure from the screenshot: a stale session cookie made re-login 403 with
    # "missing_csrf_token". The login endpoint is now CSRF-exempt, so the request reaches the
    # handler (which then rejects the bad Telegram hash) instead of being blocked.
    r = await async_client.post(
        "/api/auth/telegram",
        json={"id": 1, "first_name": "L", "hash": "bad", "auth_date": 1},
        cookies={SESSION_COOKIE_NAME: "stale-session-token"},
    )
    assert r.status_code != 403, f"login must not be CSRF-blocked, got {r.status_code}: {r.text[:160]}"
    assert "missing_csrf_token" not in r.text


async def test_bearer_request_with_vestigial_cookie_is_not_csrf_blocked(async_client):
    from app.core.security import SESSION_COOKIE_NAME

    # Bearer is the credential; a leftover cookie must not trigger CSRF enforcement. The
    # middleware exempts it, so the request reaches the handler (which rejects the bad token).
    r = await async_client.delete(
        "/api/auth/logout",
        headers={"Authorization": "Bearer garbage.token.value"},
        cookies={SESSION_COOKIE_NAME: "stale-session-token"},
    )
    assert r.status_code != 403, f"a Bearer request must not be CSRF-blocked, got {r.status_code}"
    assert "missing_csrf_token" not in r.text


async def test_cookie_only_mutation_still_requires_csrf(async_client):
    from app.core.security import SESSION_COOKIE_NAME

    # No Bearer + a session cookie + no X-CSRF-Token → CSRF is still enforced (no regression).
    r = await async_client.delete(
        "/api/auth/logout",
        cookies={SESSION_COOKIE_NAME: "ambient-session-token"},
    )
    assert r.status_code == 403
    assert r.json().get("detail") == "missing_csrf_token"
