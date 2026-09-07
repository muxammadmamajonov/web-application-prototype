"""C-1 — the sole owner must not be able to demote themselves out of the tenant.

THE DEFECT (found in the 2026-08-08 QA pass). `PATCH /api/tenant/staff/{tu_id}`
accepted a role change on the LAST owner:

    PATCH /api/tenant/staff/{own_id}  {"role": "sales"}  ->  200 OK
    owners in tenant: 0
    GET /api/tenant/settings -> 403   GET /api/tenant/billing -> 403

The tenant was left with no owner at all, and could not recover: every path
back — changing a role, revoking a member, reading settings — is itself
owner-gated. Only a platform super-admin or direct database access could undo
it, from a single unconfirmed click in the staff screen.

The guard already existed on the two NEIGHBOURING paths in the same file —
`revoke_staff` refuses with `cannot_remove_last_owner`, and the `is_active`
branch of this very handler refuses with `cannot_deactivate_last_owner`. Only
the role-change branch was missing it, which is why it went unnoticed: the
feature looked guarded from every direction except the one that mattered.

These tests pin the whole shape, not just the one refusal: demotion is legal
whenever another owner remains, and every unrelated staff edit still works.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

STAFF = "/api/tenant/staff"


@pytest_asyncio.fixture
async def ctx():
    """One tenant, one owner, one manager, one seller — plus real tokens."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole

    engine = create_async_engine(url)
    # DB tier is mandatory once TEST_DATABASE_URL is set — a setup failure must
    # fail loudly rather than self-skip green.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            t = Tenant(slug="last-owner", name="Last Owner Shop")
            s.add(t)
            await s.flush()
            owner, manager, seller = (User(telegram_id=310001),
                                      User(telegram_id=310002),
                                      User(telegram_id=310003))
            s.add_all([owner, manager, seller])
            await s.flush()
            rows = {
                "owner": TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner),
                "manager": TenantUser(tenant_id=t.id, user_id=manager.id, role=TenantRole.manager),
                "seller": TenantUser(tenant_id=t.id, user_id=seller.id, role=TenantRole.sales),
            }
            s.add_all(list(rows.values()))
            await s.commit()
            data = {
                "tenant_id": str(t.id),
                "auth": {k: {"Authorization": f"Bearer {issue_token(user_id=str(u.id))}"}
                         for k, u in (("owner", owner), ("manager", manager), ("seller", seller))},
                "tu": {k: str(v.id) for k, v in rows.items()},
                "Session": Session,
                "select": select,
                "TenantUser": TenantUser,
                "TenantRole": TenantRole,
            }
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _owner_count(data) -> int:
    from sqlalchemy import func
    async with data["Session"]() as s:
        return (await s.execute(
            data["select"](func.count(data["TenantUser"].id)).where(
                data["TenantUser"].role == data["TenantRole"].owner,
                data["TenantUser"].is_active.is_(True),
            )
        )).scalar_one()


async def test_the_last_owner_cannot_demote_themselves(ctx) -> None:
    """The exact call that emptied the tenant of owners."""
    client, data = ctx
    r = await client.patch(f"{STAFF}/{data['tu']['owner']}",
                           json={"role": "sales"}, headers=data["auth"]["owner"])
    assert r.status_code == 409, (
        f"sole owner demoted themselves and got {r.status_code} — the tenant is "
        "now unadministrable and cannot recover without platform intervention"
    )
    assert r.json()["detail"] == "cannot_remove_last_owner"
    assert await _owner_count(data) == 1, "tenant lost its last owner"


async def test_the_tenant_is_still_administrable_afterwards(ctx) -> None:
    """The point of the guard: owner-gated screens keep working."""
    client, data = ctx
    await client.patch(f"{STAFF}/{data['tu']['owner']}",
                       json={"role": "sales"}, headers=data["auth"]["owner"])
    for path in ("/api/tenant/settings", f"{STAFF}"):
        r = await client.get(path, headers=data["auth"]["owner"])
        assert r.status_code == 200, f"{path} became unreachable ({r.status_code})"


async def test_demotion_is_allowed_once_a_second_owner_exists(ctx) -> None:
    """The guard must block only the LAST owner, never owner hand-over —
    promoting a successor and stepping down is a legitimate, necessary flow."""
    client, data = ctx
    r = await client.patch(f"{STAFF}/{data['tu']['manager']}",
                           json={"role": "owner"}, headers=data["auth"]["owner"])
    assert r.status_code == 200, f"could not promote a successor: {r.text[:120]}"
    assert await _owner_count(data) == 2

    r = await client.patch(f"{STAFF}/{data['tu']['owner']}",
                           json={"role": "sales"}, headers=data["auth"]["owner"])
    assert r.status_code == 200, f"hand-over blocked: {r.status_code} {r.text[:120]}"
    assert await _owner_count(data) == 1


async def test_demoting_a_non_owner_is_unaffected(ctx) -> None:
    """The guard keys on the TARGET being the last owner, not on any role edit."""
    client, data = ctx
    r = await client.patch(f"{STAFF}/{data['tu']['seller']}",
                           json={"role": "manager"}, headers=data["auth"]["owner"])
    assert r.status_code == 200, r.text[:150]
    assert r.json()["role"] == "manager"


async def test_promoting_the_last_owner_to_owner_is_a_no_op_not_a_refusal(ctx) -> None:
    """Re-asserting the same role must not trip the guard — it removes no owner."""
    client, data = ctx
    r = await client.patch(f"{STAFF}/{data['tu']['owner']}",
                           json={"role": "owner"}, headers=data["auth"]["owner"])
    assert r.status_code == 200, r.text[:150]
    assert await _owner_count(data) == 1


async def test_deactivating_and_deleting_the_last_owner_stay_guarded(ctx) -> None:
    """The two neighbouring guards must keep working — this fix adds a third
    path, it does not replace them."""
    client, data = ctx
    r = await client.patch(f"{STAFF}/{data['tu']['owner']}",
                           json={"is_active": False}, headers=data["auth"]["owner"])
    assert r.status_code == 409
    assert r.json()["detail"] == "cannot_deactivate_last_owner"

    r = await client.delete(f"{STAFF}/{data['tu']['owner']}", headers=data["auth"]["owner"])
    assert r.status_code == 409
    assert r.json()["detail"] == "cannot_remove_last_owner"
    assert await _owner_count(data) == 1
