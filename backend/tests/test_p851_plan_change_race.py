"""(#851) /plan-change must not let two concurrent requests double-spend the prepaid
balance. Period inspection and debit are serialized with ``FOR UPDATE``; the second
same-period request sees the invoice created by the first and becomes an idempotent
success without another charge.

Real HTTP concurrency: two ``asyncio.gather``-ed POSTs go through the full app, each on
its own DB session (the get_db override yields a fresh session per request), so the
Postgres row lock is genuinely exercised.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

_PRICE = 500_000


async def test_concurrent_plan_change_cannot_double_spend_balance(migrated_db, monkeypatch) -> None:
    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import Plan, Tenant, TenantBalanceTxn, TenantUser, User
    from app.models.enums import TenantRole, TenantStatus
    from app.services import audit

    # Widen the lock-hold window: the audit write runs after the debit while the tenant
    # row is still locked (before commit), so yielding here for a beat makes the second
    # request genuinely contend on the FOR UPDATE lock — otherwise the in-process
    # httpx/ASGI harness serializes the two requests and the race never manifests.
    async def _slow_record(*_a, **_k):
        await asyncio.sleep(0.25)

    monkeypatch.setattr(audit, "record", _slow_record)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    # A paid plan priced at _PRICE, and a tenant with EXACTLY that balance (enough for
    # one period) + an owner who can self-select a plan.
    async with Session() as s:
        plan = Plan(code="p851-pro", name_uz="Pro", name_ru="Pro", price_monthly=Decimal(_PRICE))
        t = Tenant(slug="p851-t", name="P851 T", plan_code="trial",
                   status=TenantStatus.active, balance=Decimal(_PRICE))
        u = User(telegram_id=851851)
        s.add_all([plan, t, u])
        await s.flush()
        s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner))
        tid, uid = t.id, u.id
        await s.commit()

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    auth = {"Authorization": f"Bearer {issue_token(user_id=str(uid))}", "X-Tenant-Id": str(tid)}
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            async def _change():
                return await client.post(
                    "/api/tenant/plan-change", headers=auth, json={"plan_code": "p851-pro"},
                )
            r1, r2 = await asyncio.gather(_change(), _change())
    finally:
        app.dependency_overrides.pop(get_db, None)

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 200], (
        f"a duplicate same-period plan change must be an idempotent success — "
        f"got {codes}: {r1.text[:150]} | {r2.text[:150]}"
    )

    # The balance was debited exactly ONCE, and exactly one plan_charge ledger row exists.
    async with Session() as s:
        bal = (await s.execute(select(Tenant.balance).where(Tenant.id == tid))).scalar_one()
        assert int(bal) == 0, f"balance must be debited exactly once (→0), got {bal}"
        charges = (await s.execute(
            select(func.count()).select_from(TenantBalanceTxn).where(
                TenantBalanceTxn.tenant_id == tid, TenantBalanceTxn.kind == "plan_charge",
            )
        )).scalar_one()
        assert charges == 1, f"exactly one plan_charge ledger row, got {charges}"
