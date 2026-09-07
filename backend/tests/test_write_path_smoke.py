"""Write-path smoke: an authenticated owner can actually MUTATE through the full
middleware stack (CSRF, security headers, body-size, …) and reach the handler.

This is the coverage that was MISSING when #843 shipped: every existing mutation test
asserted a *failure* path (401/403/404), so the CSRF-on-Bearer bug — which 403'd every
Bearer-authenticated mutation and broke the entire frontend write path — sailed through
CI. These assert the happy path: a Bearer POST/PATCH/DELETE succeeds (is NOT blocked at
403 by a middleware), so a future regression of that class fails a test instead of the
production app.
"""
from __future__ import annotations


async def test_authenticated_owner_can_create_customer(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    r = await client.post(
        "/api/tenant/customers",
        headers={**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]},
        json={"full_name": "New Customer", "phone": "+998901112233"},
    )
    assert r.status_code == 201, (
        f"a Bearer-authenticated create must reach the handler (not be 403'd by a "
        f"middleware) — got {r.status_code}: {r.text[:200]}"
    )
    assert r.json()["full_name"] == "New Customer"


async def test_authenticated_owner_can_patch_own_customer(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    r = await client.patch(
        f"/api/tenant/customers/{ctx['customer_a']}",
        headers={**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]},
        json={"full_name": "Alice Renamed"},
    )
    assert r.status_code == 200, (
        f"a Bearer-authenticated patch of an own-tenant row must succeed — "
        f"got {r.status_code}: {r.text[:200]}"
    )


async def test_authenticated_owner_can_delete_own_customer(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    r = await client.delete(
        f"/api/tenant/customers/{ctx['customer_a']}",
        headers={**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]},
    )
    assert r.status_code in (200, 204), (
        f"a Bearer-authenticated delete of an own-tenant row must succeed — "
        f"got {r.status_code}: {r.text[:200]}"
    )
