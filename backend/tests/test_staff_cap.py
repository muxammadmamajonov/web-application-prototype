"""(#135) locked_staff_decision enforces max_staff while holding a tenant-row lock,
so concurrent staff adds / invite-claims can't both pass the check and exceed the
plan cap. Only *active* members count."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Plan, Tenant, TenantUser, User
from app.models.enums import TenantRole
from app.services import plan_limits


async def test_locked_staff_decision_enforces_max_staff(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        s.add(Plan(code="cap2", name_uz="Cap", name_ru="Cap", price_monthly=0, max_staff=2))
        t = Tenant(slug="capco", name="Cap Co", plan_code="cap2")
        s.add(t)
        await s.flush()

        async def add_staff(tg: int, active: bool) -> None:
            u = User(telegram_id=tg)
            s.add(u)
            await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.sales, is_active=active))
            await s.commit()

        # 0 active → one more fits (0 + 1 <= 2)
        d = await plan_limits.locked_staff_decision(s, t.id)
        assert d.allowed and d.limit == 2

        await add_staff(950001, active=True)   # 1 active
        assert (await plan_limits.locked_staff_decision(s, t.id)).allowed   # 1 + 1 <= 2

        await add_staff(950002, active=False)  # inactive — ignored
        assert (await plan_limits.locked_staff_decision(s, t.id)).allowed   # still 1 active

        await add_staff(950003, active=True)   # 2 active → at cap
        assert not (await plan_limits.locked_staff_decision(s, t.id)).allowed  # 2 + 1 > 2
