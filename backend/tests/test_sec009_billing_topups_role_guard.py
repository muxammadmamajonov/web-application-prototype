"""SEC-009 — `GET /api/tenant/billing/topups` must enforce owner/manager server-side.

The read was tenant-bound but role-ungated, so any active member of the tenant —
including a `sales` seller (the 'Xodim · Sotuvchi' role) — could pull the shop's billing top-up history with a
direct API call. The UI hid the screen, which is not an access control.

Sibling billing mutations already call `_require_owner_or_manager`; this read did not.
DB-backed, exercised through the real `resolve_tenant` dependency and a real token.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

TOPUPS = "/api/tenant/billing/topups"


@pytest_asyncio.fixture
async def roles_ctx():
    """One tenant with an owner, a manager and a staff member, plus real tokens."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole

    engine = create_async_engine(url)
    # (QA-R-002) DB tier is mandatory once TEST_DATABASE_URL is set — setup errors
    # (refused connection, privilege, bad schema) must FAIL, never self-skip green.
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
            t = Tenant(slug="tenant-roles", name="Roles Tenant")
            s.add(t)
            await s.flush()
            owner, manager, staff = (User(telegram_id=200001),
                                     User(telegram_id=200002),
                                     User(telegram_id=200003))
            s.add_all([owner, manager, staff])
            await s.flush()
            s.add_all([
                TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner),
                TenantUser(tenant_id=t.id, user_id=manager.id, role=TenantRole.manager),
                TenantUser(tenant_id=t.id, user_id=staff.id, role=TenantRole.sales),
            ])
            await s.commit()
            ctx = {
                "owner": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
                "manager": {"Authorization": f"Bearer {issue_token(user_id=str(manager.id))}"},
                "staff": {"Authorization": f"Bearer {issue_token(user_id=str(staff.id))}"},
            }
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, ctx
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def test_staff_is_denied_billing_topups(roles_ctx) -> None:
    """The defect: a sales-role seller could read the shop's billing history directly."""
    client, ctx = roles_ctx
    r = await client.get(TOPUPS, headers=ctx["staff"])
    assert r.status_code == 403, f"sales role must not read billing top-ups, got {r.status_code}"
    assert r.json()["detail"] == "owner_or_manager_only"


async def test_owner_and_manager_retain_access(roles_ctx) -> None:
    """The fix must not narrow the roles that legitimately use this screen."""
    client, ctx = roles_ctx
    for role in ("owner", "manager"):
        r = await client.get(TOPUPS, headers=ctx[role])
        assert r.status_code == 200, f"{role} lost access: {r.status_code}"
        assert isinstance(r.json(), list)


async def test_unauthenticated_is_still_rejected(roles_ctx) -> None:
    client, _ = roles_ctx
    r = await client.get(TOPUPS)
    assert r.status_code == 401


async def test_response_contract_and_pagination_unchanged(roles_ctx) -> None:
    """The guard must not alter the list contract or the offset parameter."""
    client, ctx = roles_ctx
    r = await client.get(TOPUPS, headers=ctx["owner"])
    assert r.status_code == 200
    assert r.json() == []
    r2 = await client.get(f"{TOPUPS}?offset=0", headers=ctx["owner"])
    assert r2.status_code == 200
    assert r2.json() == []
