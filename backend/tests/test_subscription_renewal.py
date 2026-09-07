"""Subscription billing-cycle E2E tests.

Drives the real billing cron entry points against the actual models. Billing keys
off ``tenant.plan_code`` → the matching ``Plan`` (via ``effective_config``), NOT a
separate subscription row — so these create a Plan + a Tenant on that plan and assert:

- ``run_billing_cycle`` issues exactly one invoice per tenant per period;
- ``run_dunning`` suspends a tenant whose invoice is past the suspend threshold;
- a second ``run_billing_cycle`` for the same period issues nothing new (idempotent),
  and the returned count reflects only NEWLY-issued invoices.

(The previous version of this file was scaffolded against an imagined schema —
``CurrencyCode``, ``Plan(name=…, monthly_amount=…)``, ``TenantSubscription``-driven
billing — none of which exist; rewritten to the real API.)
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Invoice, Plan, Tenant
from app.models.enums import InvoiceStatus, TenantStatus


async def _make_plan_and_tenant(engine, *, slug: str, price: Decimal = Decimal("500000")):
    """A fixed-price Plan + an active Tenant subscribed to it (by ``plan_code``)."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        plan = Plan(code=f"{slug}-plan", name_uz="Pro", name_ru="Про", price_monthly=price)
        s.add(plan)
        await s.flush()
        tenant = Tenant(slug=slug, name=slug.upper(), plan_code=plan.code, status=TenantStatus.active)
        s.add(tenant)
        await s.flush()
        tid = tenant.id
        await s.commit()
    return tid


async def test_subscription_renewal_invoice_generation(migrated_db) -> None:
    """Plan + tenant → run_billing_cycle issues exactly one invoice for the period."""
    from app.services.billing import run_billing_cycle

    tid = await _make_plan_and_tenant(migrated_db, slug="billing-t")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        await run_billing_cycle(s, today=date(2026, 6, 1))
        await s.commit()

    async with Session() as s:
        invs = (await s.execute(select(Invoice).where(Invoice.tenant_id == tid))).scalars().all()
    assert len(invs) == 1
    inv = invs[0]
    assert inv.status == InvoiceStatus.open
    assert inv.amount == Decimal("500000")          # the plan's price_monthly
    assert inv.period_start == date(2026, 6, 1)
    assert inv.period_end == date(2026, 6, 30)


async def test_subscription_renewal_dunning_suspend(migrated_db) -> None:
    """An invoice past the suspend threshold (3d overdue, under the 14d block) drives
    the tenant to ``suspended``."""
    from app.services.billing import run_dunning

    tid = await _make_plan_and_tenant(migrated_db, slug="dunning-t")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)

    async with Session() as s:
        tenant = await s.get(Tenant, tid)
        s.add(Invoice(
            tenant_id=tid, plan_code=tenant.plan_code,
            period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
            amount=Decimal("500000"), status=InvoiceStatus.open,
            issued_at=now - timedelta(days=17),
            due_at=now - timedelta(days=10),   # 10 days overdue
        ))
        await s.commit()

    async with Session() as s:
        await run_dunning(s, now=now)
        await s.commit()

    async with Session() as s:
        tenant = await s.get(Tenant, tid)
        assert tenant.status == TenantStatus.suspended
        assert tenant.suspended_at is not None


async def test_subscription_renewal_idempotent_invoice(migrated_db) -> None:
    """A second run_billing_cycle for the same period issues nothing new — and the
    returned count reflects only newly-issued invoices (0 on the re-run)."""
    from app.services.billing import run_billing_cycle

    tid = await _make_plan_and_tenant(migrated_db, slug="idempotent-t")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        issued1 = await run_billing_cycle(s, today=date(2026, 6, 1))
        await s.commit()
    async with Session() as s:
        issued2 = await run_billing_cycle(s, today=date(2026, 6, 1))
        await s.commit()

    assert issued1 >= 1     # at least our tenant was billed
    assert issued2 == 0     # the re-run issues nothing new (only NEW invoices counted)

    async with Session() as s:
        n = (await s.execute(
            select(func.count()).select_from(Invoice).where(Invoice.tenant_id == tid)
        )).scalar_one()
    assert n == 1           # exactly one invoice — no double-billing


async def test_new_year_invoice_uses_tashkent_period_and_sequence(migrated_db) -> None:
    """00:30 on Jan 1 in Tashkent is still Dec 31 in UTC, but it belongs to
    January and to the new year's legal invoice sequence."""
    from app.services.billing import generate_invoice

    tid = await _make_plan_and_tenant(migrated_db, slug="new-year-t")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    instant = datetime(2025, 12, 31, 19, 30, tzinfo=UTC)

    async with Session() as s:
        tenant = await s.get(Tenant, tid)
        inv = await generate_invoice(s, tenant, issued_at=instant)
        await s.commit()

    assert inv is not None
    assert inv.period_start == date(2026, 1, 1)
    assert inv.period_end == date(2026, 1, 31)
    assert inv.invoice_number.startswith("2026-")
