"""#79 — HTTP-level auth gate: the protected API surface rejects anonymous and
bad-token callers (401) before any DB query, and public liveness stays open.

These run against the ASGI app via httpx (no network, no live DB): the auth
dependency (app.core.security.current_user) raises 401 for a missing/garbage
token before the endpoint touches the database. This is a fast, DB-free guard
against accidentally shipping an unauthenticated endpoint — the exact #64/#65 P0
class. DB-backed tenant-isolation lives in test_tenant_isolation.py (needs a real
Postgres; runs in CI).
"""
from __future__ import annotations

import pytest

# Representative protected endpoints across every authenticated router. Each must
# reject an anonymous caller.
PROTECTED: list[tuple[str, str]] = [
    ("GET", "/api/tenant/customers"),
    ("GET", "/api/tenant/guarantors"),
    ("GET", "/api/tenant/invoices"),
    ("GET", "/api/tenant/customers/00000000-0000-0000-0000-000000000000"),
    ("GET", "/api/me/installments"),
    ("GET", "/api/me/summary"),
    ("GET", "/api/platform/tenants"),
    ("GET", "/api/platform/promos"),
]


@pytest.mark.parametrize("method, path", PROTECTED)
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_protected_route_rejects_anonymous(async_client, method: str, path: str) -> None:
    r = await async_client.request(method, path)
    assert r.status_code in (401, 403), f"{method} {path} returned {r.status_code}, expected 401/403"


async def test_garbage_bearer_token_rejected(async_client) -> None:
    r = await async_client.get(
        "/api/tenant/customers",
        headers={"Authorization": "Bearer not.a.real.jwt"},
    )
    assert r.status_code == 401


async def test_health_is_public(async_client) -> None:
    r = await async_client.get("/health")
    assert r.status_code == 200


async def test_auth_debug_disabled_in_production(async_client, monkeypatch) -> None:
    """(#131) /api/auth/debug echoes decoded JWT claims + user PII and is
    unauthenticated — it must 404 in production (works only in local/staging)."""
    from app.config import settings
    # Non-prod (test env): the endpoint responds (no-bearer → {"ok": false}, 200).
    r = await async_client.get("/api/auth/debug")
    assert r.status_code == 200
    # Simulate production: the info-leak surface disappears.
    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))
    r = await async_client.get("/api/auth/debug")
    assert r.status_code == 404
