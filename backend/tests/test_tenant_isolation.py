"""#79 — tenant data isolation over real HTTP.

A user authenticated for tenant A must not be able to read tenant B's data, by
selecting B via X-Tenant-Id (rejected at resolve_tenant) or by addressing one of
B's resources directly (scoped 404). These run against a real Postgres through
httpx; they SKIP unless TEST_DATABASE_URL is set (the CI `integration` job
provides a postgres service). See conftest.isolation_ctx for the seeded fixture.
"""
from __future__ import annotations


async def test_user_cannot_enter_another_tenant(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    # User A asks to act as tenant B (A is not a member) → 404, not B's data.
    r = await client.get(
        "/api/tenant/customers",
        headers={**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_b"]},
    )
    assert r.status_code == 404


async def test_list_is_scoped_to_own_tenant(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    r = await client.get(
        "/api/tenant/customers",
        headers={**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]},
    )
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()["items"]}
    assert ctx["customer_a"] in ids
    assert ctx["customer_b"] not in ids  # B's customer must never appear


async def test_cannot_read_other_tenants_customer_by_id(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    # Even inside A's own context, addressing B's customer id is a scoped 404.
    r = await client.get(
        f"/api/tenant/customers/{ctx['customer_b']}",
        headers={**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]},
    )
    assert r.status_code == 404


async def test_cannot_patch_other_tenants_customer(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    # Write isolation: A (in A's context) must not be able to mutate B's customer.
    r = await client.patch(
        f"/api/tenant/customers/{ctx['customer_b']}",
        headers={**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]},
        json={"full_name": "Hacked By A"},
    )
    assert r.status_code == 404  # scoped — B's row is invisible, so not found


async def test_cannot_delete_other_tenants_customer(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    r = await client.delete(
        f"/api/tenant/customers/{ctx['customer_b']}",
        headers={**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]},
    )
    assert r.status_code == 404
