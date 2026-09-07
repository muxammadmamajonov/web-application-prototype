"""(#746/#843/#257) CSRFValidationMiddleware — real double-submit CSRF validation.

A COOKIE-authenticated state change must carry an ``X-CSRF-Token`` header whose value
MATCHES the readable ``muddato.csrf`` cookie (double-submit). (#257 upgraded the prior
*presence-only* check — which any value satisfied — to real verification.) A Bearer /
no-cookie request can't be CSRF-forged, so it is EXEMPT — otherwise the middleware 403s
every Bearer mutation and breaks the frontend (#843). Failures answer with a clean 403,
never a 500 (raising inside BaseHTTPMiddleware would escape FastAPI's ExceptionMiddleware
and surface as 500 — #746).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.observability import CSRFValidationMiddleware
from app.core.security import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME


def _client(*, session: bool = False, csrf: str | None = None) -> TestClient:
    app = FastAPI()
    app.add_middleware(CSRFValidationMiddleware)

    @app.post("/things")
    async def create_thing() -> dict[str, str]:  # pragma: no cover - body trivial
        return {"ok": "yes"}

    @app.get("/things")
    async def list_things() -> dict[str, str]:  # pragma: no cover - body trivial
        return {"ok": "yes"}

    # raise_server_exceptions=False so a regression (raise -> 500) is observable
    # as a 500 response rather than re-raised, making the assertion explicit.
    tc = TestClient(app, raise_server_exceptions=False)
    if session:
        tc.cookies.set(SESSION_COOKIE_NAME, "sess-token")  # the ambient credential
    if csrf is not None:
        tc.cookies.set(CSRF_COOKIE_NAME, csrf)
    return tc


# ── cookie-authenticated mutations: real double-submit validation ────────────

def test_cookie_post_without_csrf_header_is_clean_403() -> None:
    resp = _client(session=True, csrf="tok").post("/things")
    assert resp.status_code == 403, f"expected clean 403, got {resp.status_code}"
    assert resp.json()["detail"] == "missing_csrf_token"


def test_cookie_post_with_matching_token_passes() -> None:
    resp = _client(session=True, csrf="tok").post("/things", headers={"X-CSRF-Token": "tok"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": "yes"}


def test_cookie_post_with_mismatched_token_is_403() -> None:
    resp = _client(session=True, csrf="tok").post("/things", headers={"X-CSRF-Token": "WRONG"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "invalid_csrf_token"


def test_cookie_post_with_header_but_no_csrf_cookie_is_403() -> None:
    # The OLD presence-only check let this through (any header value passed); real
    # double-submit rejects it because there's no cookie to match — this is the #257 fix.
    resp = _client(session=True).post("/things", headers={"X-CSRF-Token": "tok"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "invalid_csrf_token"


# ── exempt: Bearer / no-cookie / safe methods (not CSRF-forgeable) ────────────

def test_bearer_post_is_exempt() -> None:
    resp = _client().post("/things", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200, (
        f"a Bearer mutation must NOT require a CSRF token — got {resp.status_code}"
    )


def test_no_session_cookie_post_is_exempt() -> None:
    # No ambient session cookie → nothing CSRF could exploit → exempt.
    resp = _client(csrf="tok").post("/things")
    assert resp.status_code == 200


def test_safe_method_is_exempt_even_with_cookie() -> None:
    resp = _client(session=True, csrf="tok").get("/things")
    assert resp.status_code == 200
