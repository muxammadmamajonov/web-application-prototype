"""(#246) settle_invoices_from_balance: prepaid balance clears open invoices (whole only)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Invoice, Tenant
from app.models.enums import InvoiceStatus
from app.services.billing import settle_invoices_from_balance


def _inv(tid, i: int, amt: int) -> Invoice:
    return Invoice(
        tenant_id=tid, plan_code="lite", invoice_number=f"2026-{i:06d}",
        period_start=date(2026, i, 1), period_end=date(2026, i, 28),
        active_installments_count=0, amount=Decimal(amt), status=InvoiceStatus.open,
        line_items={}, issued_at=datetime.now(UTC), due_at=datetime.now(UTC),
    )


async def test_settle_pays_whole_invoices_oldest_first(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="paysettle", name="Pay Settle", plan_code="lite", balance=Decimal("100000"))
        s.add(t)
        await s.flush()
        s.add_all([_inv(t.id, 1, 40000), _inv(t.id, 2, 40000), _inv(t.id, 3, 50000)])
        await s.flush()

        n = await settle_invoices_from_balance(s, t)
        # 40k + 40k = 80k <= 100k settles; the next (50k) would overshoot the 20k left
        assert n == 2
        assert t.balance == Decimal("20000")
        paid = (await s.execute(
            select(func.count(Invoice.id)).where(
                Invoice.tenant_id == t.id, Invoice.status == InvoiceStatus.paid,
            )
        )).scalar_one()
        assert paid == 2

        # idempotent: re-running with the leftover 20k settles nothing more (50k > 20k)
        assert await settle_invoices_from_balance(s, t) == 0
        assert t.balance == Decimal("20000")


async def test_no_balance_no_settle(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="nobalance", name="No Balance", plan_code="lite", balance=Decimal("0"))
        s.add(t)
        await s.flush()
        s.add(_inv(t.id, 4, 40000))
        await s.flush()
        assert await settle_invoices_from_balance(s, t) == 0
