"""(#257) Session + double-submit CSRF cookie issuance.

``establish_session`` must set BOTH cookies with the correct, security-critical
attributes: the session cookie HttpOnly (XSS can't read it) and the CSRF cookie NOT
HttpOnly (the SPA must read it to echo it back in the X-CSRF-Token header).
"""
from __future__ import annotations

from starlette.responses import Response

from app.core.security import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    establish_session,
    issue_csrf_token,
)


def _set_cookie_headers(resp: Response) -> list[str]:
    return [v.decode() for k, v in resp.raw_headers if k == b"set-cookie"]


def test_establish_session_sets_both_cookies() -> None:
    resp = Response()
    establish_session(resp, "jwt.token.here")
    blob = " ".join(_set_cookie_headers(resp))
    assert SESSION_COOKIE_NAME in blob
    assert CSRF_COOKIE_NAME in blob


def test_session_cookie_is_httponly_but_csrf_cookie_is_readable() -> None:
    resp = Response()
    establish_session(resp, "jwt.token.here")
    headers = _set_cookie_headers(resp)
    session_h = next(h for h in headers if h.startswith(SESSION_COOKIE_NAME + "="))
    csrf_h = next(h for h in headers if h.startswith(CSRF_COOKIE_NAME + "="))
    # Session cookie MUST be HttpOnly; CSRF cookie MUST NOT be (double-submit needs JS read).
    assert "httponly" in session_h.lower()
    assert "httponly" not in csrf_h.lower()
    # Both pin SameSite=Strict.
    assert "samesite=strict" in session_h.lower()
    assert "samesite=strict" in csrf_h.lower()


def test_csrf_tokens_are_unique_and_unguessable() -> None:
    a, b = issue_csrf_token(), issue_csrf_token()
    assert a != b
    assert len(a) >= 32  # secrets.token_urlsafe(32) → ~43 chars
