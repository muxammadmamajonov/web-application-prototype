"""#753 — baseline security headers on every response.

``SecurityHeadersMiddleware`` (app/core/observability.py) stamps
``X-Content-Type-Options: nosniff`` and siblings onto every API response. It is
added last in app/main.py so it runs OUTERMOST — the headers must therefore be
present on success (200), auth-gate (401), and not-found (404) responses alike.

These are DB-free: they ride the ASGITransport ``async_client`` fixture and never
open a socket (the 401 case short-circuits in the auth dependency before any DB
query; 404 never reaches a handler).
"""
from __future__ import annotations

import httpx
import pytest

# The three headers the middleware sets unconditionally (HSTS is production-only,
# asserted separately below).
BASELINE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

# (path, expected_status) covering the three response classes the middleware must
# stamp: a 200 liveness probe, a 401 auth gate, and a 404 unknown route.
_CASES = [
    ("/health", 200),
    ("/api/tenant/overview", 401),
    ("/this-route-does-not-exist", 404),
]


@pytest.mark.parametrize("path, expected_status", _CASES)
async def test_security_headers_present_on_every_response(
    async_client: httpx.AsyncClient, path: str, expected_status: int
) -> None:
    resp = await async_client.get(path)
    # Guard the precondition so a future routing change can't silently turn a 401
    # into a 200 and weaken what this test actually exercises.
    assert resp.status_code == expected_status, f"{path} -> {resp.status_code}"
    for header, value in BASELINE_HEADERS.items():
        assert resp.headers.get(header) == value, (
            f"{path} ({expected_status}) missing/incorrect {header}: "
            f"{resp.headers.get(header)!r}"
        )


async def test_nosniff_specifically_present(async_client: httpx.AsyncClient) -> None:
    """The exact finding from #753: MIME-sniffing protection on responses."""
    resp = await async_client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


async def test_hsts_is_production_only(async_client: httpx.AsyncClient) -> None:
    """HSTS is gated on settings.is_production; the test env is ``development``,
    so it must NOT be advertised (avoid pinning non-TLS dev clients to HTTPS)."""
    from app.config import settings

    resp = await async_client.get("/health")
    if settings.is_production:
        assert "Strict-Transport-Security" in resp.headers
    else:
        assert "Strict-Transport-Security" not in resp.headers
