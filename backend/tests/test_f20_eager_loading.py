"""F-20 — eager-loading convention: GET /auth/me must not N+1 over memberships.

A user in N tenants previously fired one lazy ``SELECT ... FROM tenants`` per
membership. ``selectinload(TenantUser.tenant)`` collapses that into a single
batched ``... WHERE tenants.id IN (...)``. This test counts the tenant-table
SELECTs issued while serving /me for a user in 3 tenants and asserts it's one.
"""
from __future__ import annotations

import re

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker


def test_f20_me_query_uses_selectinload() -> None:
    """Source guard: the /me handler eager-loads the membership's tenant."""
    from pathlib import Path

    src = Path("app/api/auth.py").read_text()
    assert "selectinload(TenantUser.tenant)" in src


async def test_f20_me_eager_loads_tenants_no_n_plus_1(migrated_db) -> None:
    import httpx
    from httpx import ASGITransport

    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole, TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        u = User(telegram_id=200200)
        s.add(u); await s.flush()
        for i in range(3):
            t = Tenant(slug=f"f20-{i}", name=f"T{i}", plan_code="trial", status=TenantStatus.active)
            s.add(t); await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner))
        uid = u.id
        await s.commit()

    # Count statements that read the tenants table while serving the request.
    tenants_selects: list[str] = []

    def _before(conn, cursor, statement, params, context, executemany):
        if re.search(r"\bFROM tenants\b", statement):
            tenants_selects.append(statement)

    event.listen(migrated_db.sync_engine, "before_cursor_execute", _before)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/auth/me",
                            headers={"Authorization": f"Bearer {issue_token(user_id=str(uid))}"})
    finally:
        app.dependency_overrides.pop(get_db, None)
        event.remove(migrated_db.sync_engine, "before_cursor_execute", _before)

    assert r.status_code == 200, r.text[:200]
    assert len(r.json()["tenants"]) == 3
    # selectinload => exactly one batched read of the tenants table (not one per
    # membership). The pre-fix lazy loop produced 3.
    assert len(tenants_selects) == 1, (
        f"expected a single batched tenants SELECT, got {len(tenants_selects)} "
        f"(N+1 regression): {tenants_selects}"
    )
