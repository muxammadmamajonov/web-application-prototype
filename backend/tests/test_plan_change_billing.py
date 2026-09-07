"""Changing plan and the monthly cron must bill one period exactly once."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker


async def test_plan_change_and_same_period_cron_charge_only_once(migrated_db) -> None:
    from app.api.tenant.billing import PlanChangeRequest, tenant_plan_change
    from app.models import Invoice, Plan, Tenant, TenantBalanceTxn, TenantUser, User
    from app.models.enums import InvoiceStatus, TenantRole, TenantStatus
    from app.services.billing import run_billing_cycle

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        plan = Plan(code="single-period", name_uz="Pro", name_ru="Pro",
                    price_monthly=Decimal("500000"))
        tenant = Tenant(slug="single-period", name="Single Period", plan_code="trial",
                        status=TenantStatus.demo, balance=Decimal("1000000"))
        user = User(telegram_id=777001)
        s.add_all([plan, tenant, user])
        await s.flush()
        membership = TenantUser(
            tenant_id=tenant.id, user_id=user.id, role=TenantRole.owner, is_active=True,
        )
        s.add(membership)
        await s.commit()
        tenant_id = tenant.id

        await tenant_plan_change(
            body=PlanChangeRequest(plan_code=plan.code),
            ctx=(tenant, membership), db=s,
        )

    async with Session() as s:
        await run_billing_cycle(s, today=date.today())

    async with Session() as s:
        tenant = await s.get(Tenant, tenant_id)
        invoices = (await s.execute(
            select(Invoice).where(Invoice.tenant_id == tenant_id)
        )).scalars().all()
        charges = (await s.execute(
            select(func.count(TenantBalanceTxn.id)).where(
                TenantBalanceTxn.tenant_id == tenant_id,
                TenantBalanceTxn.kind.in_(["plan_charge", "invoice_settlement"]),
            )
        )).scalar_one()
        assert tenant.balance == Decimal("500000")
        assert len(invoices) == 1
        assert invoices[0].status is InvoiceStatus.paid
        assert charges == 1
