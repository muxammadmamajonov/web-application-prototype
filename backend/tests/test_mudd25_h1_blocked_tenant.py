"""MUDD-25 H1 regression: a blocked/suspended tenant must not be able to mutate
data, but reads must keep working.

The previous attempt at this test (lost with an earlier agent's worktree, then
reintroduced as a placeholder in commit 1c862b8) only asserted that the
``TenantStatus`` enum has ``blocked``/``suspended`` members — it never called
the endpoint, so it would pass identically whether or not ``resolve_tenant``'s
fail-closed guard (``backend/app/api/tenant/_shared.py``) actually rejects the
request. This replaces it with real HTTP calls through the full FastAPI stack.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import _pg_test_url


async def _set_tenant_status(tenant_id: str, status_value: str) -> None:
    url = _pg_test_url()
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE tenants SET status = :status WHERE id = :id"),
                {"status": status_value, "id": tenant_id},
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("blocking_status", ["blocked", "suspended"])
async def test_blocked_tenant_rejects_mutation(isolation_ctx, blocking_status: str) -> None:
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    await _set_tenant_status(ctx["tenant_a"], blocking_status)

    resp = await client.post(
        "/api/tenant/customers", headers=auth,
        json={"full_name": "Should Not Be Created", "phone": "+998901234567"},
    )

    assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text[:300]}"
    assert resp.json().get("detail") == "tenant_blocked_or_suspended"


async def test_blocked_tenant_still_allows_reads(isolation_ctx) -> None:
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    await _set_tenant_status(ctx["tenant_a"], "blocked")

    resp = await client.get("/api/tenant/customers", headers=auth)

    assert resp.status_code == 200, f"expected reads to stay open, got {resp.status_code}: {resp.text[:300]}"


async def test_active_tenant_allows_mutation(isolation_ctx) -> None:
    """Regression guard: the fail-closed guard must not fire for a normal, active
    tenant — otherwise every write in the product would 403."""
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    resp = await client.post(
        "/api/tenant/customers", headers=auth,
        json={"full_name": "Active Tenant Customer", "phone": "+998901234568"},
    )

    assert resp.status_code == 201, f"expected create to succeed, got {resp.status_code}: {resp.text[:300]}"
