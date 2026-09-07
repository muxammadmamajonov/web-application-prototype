"""(#854) The billing-cycle settle must lock the tenant row before its balance
read-modify-write, so a concurrent (locked) top-up's credit isn't clobbered by a stale
settle write — a lost top-up.

``run_billing_cycle`` loads tenants with a plain unlocked SELECT; the fix is a
``FOR UPDATE`` refresh in ``generate_invoice`` right before ``settle_invoices_from_balance``.
This races a billing settle (which debits a freshly-issued invoice from balance) against a
concurrent locked credit on the same tenant and asserts BOTH apply.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

_BAL0 = 100
_PRICE = 30
_CREDIT = 50


async def test_billing_settle_does_not_clobber_concurrent_credit(migrated_db) -> None:
    from app.models import Invoice, Plan, Tenant
    from app.models.enums import InvoiceStatus, TenantStatus
    from app.services.billing import generate_invoice

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        s.add(Plan(code="p854-plan", name_uz="P854", name_ru="P854",
                   price_monthly=Decimal(_PRICE)))
        t = Tenant(slug="p854-t", name="P854 T", plan_code="p854-plan",
                   status=TenantStatus.active, balance=Decimal(_BAL0))
        s.add(t)
        await s.flush()
        tid = t.id
        await s.commit()

    # A concurrent locked credit — models a Click/Payme top-up, an admin credit, or a
    # referral bonus, all of which lock the tenant FOR UPDATE. Holds the lock briefly so
    # the billing settle must genuinely contend on it.
    async def _credit():
        async with Session() as s:
            t = (await s.execute(
                select(Tenant).where(Tenant.id == tid).with_for_update()
            )).scalar_one()
            t.balance = (t.balance or Decimal(0)) + _CREDIT
            await asyncio.sleep(0.25)
            await s.commit()

    # The billing cycle: load the tenant UNLOCKED (exactly as run_billing_cycle does),
    # then generate_invoice issues this period's invoice and settles it from balance.
    async def _billing():
        async with Session() as s:
            t = (await s.execute(select(Tenant).where(Tenant.id == tid))).scalar_one()
            inv = await generate_invoice(s, t)
            await s.commit()
            return inv

    inv, _ = await asyncio.gather(_billing(), _credit())
    assert inv is not None, "billing should have issued an invoice"

    async with Session() as s:
        bal = (await s.execute(select(Tenant.balance).where(Tenant.id == tid))).scalar_one()
        # Both must apply: 100 start, +50 credit, -30 settled invoice = 120. A lost update
        # (settle clobbers the credit with a stale write) would leave 70.
        assert int(bal) == _BAL0 + _CREDIT - _PRICE, (
            f"both the credit and the settle must apply (no lost update): "
            f"expected {_BAL0 + _CREDIT - _PRICE}, got {bal}"
        )
        # Sanity: the freshly-issued invoice was actually settled from balance.
        statuses = (await s.execute(
            select(Invoice.status).where(Invoice.tenant_id == tid)
        )).scalars().all()
        assert statuses and all(st == InvoiceStatus.paid for st in statuses), (
            f"the issued invoice should be settled to paid, got {statuses}"
        )
