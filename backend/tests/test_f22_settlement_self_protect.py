"""(F-22) ``settle_invoices_from_balance`` must be self-protecting: it locks the
candidate invoices FOR UPDATE and re-checks each still-unpaid under the lock, so
two concurrent settlers can't double-settle the same invoice or double-spend the
balance — even if a caller forgets to lock the tenant row first.

DB-backed (self-skips without TEST_DATABASE_URL via ``migrated_db``).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker


def _inv(tid, i: int, amt: int):
    from app.models import Invoice
    from app.models.enums import InvoiceStatus

    return Invoice(
        tenant_id=tid, plan_code="lite", invoice_number=f"2026-{i:06d}",
        period_start=date(2026, i, 1), period_end=date(2026, i, 28),
        active_installments_count=0, amount=Decimal(amt), status=InvoiceStatus.open,
        line_items={}, issued_at=datetime.now(UTC), due_at=datetime.now(UTC),
    )


async def test_settle_only_decrements_by_rows_it_transitions(migrated_db) -> None:
    """Two settlers race on the SAME tenant + invoices. The candidate FOR UPDATE lock
    serializes them; the loser re-reads each invoice as already paid and skips it, so
    each invoice is settled exactly once and the balance is debited exactly once."""
    from app.models import Invoice, Tenant
    from app.models.enums import InvoiceStatus
    from app.services.billing import settle_invoices_from_balance

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="f22-race", name="F22", plan_code="lite", balance=Decimal("100000"))
        s.add(t)
        await s.flush()
        tid = t.id
        s.add_all([_inv(tid, 1, 30000), _inv(tid, 2, 30000), _inv(tid, 3, 30000)])
        await s.commit()

    # Each worker loads its own Tenant instance (unlocked, as a naive caller might),
    # then settles. The candidate-invoice FOR UPDATE lock is what must protect us.
    async def _settle() -> int:
        async with Session() as s:
            t = (await s.execute(select(Tenant).where(Tenant.id == tid))).scalar_one()
            n = await settle_invoices_from_balance(s, t)
            await s.commit()
            return n

    n1, n2 = await asyncio.gather(_settle(), _settle())

    from app.models import TenantBalanceTxn

    async with Session() as s:
        paid = (await s.execute(
            select(func.count(Invoice.id)).where(
                Invoice.tenant_id == tid, Invoice.status == InvoiceStatus.paid,
            )
        )).scalar_one()
        bal = (await s.execute(select(Tenant.balance).where(Tenant.id == tid))).scalar_one()
        txns = (await s.execute(
            select(func.count(TenantBalanceTxn.id)).where(TenantBalanceTxn.tenant_id == tid)
        )).scalar_one()

    # All three invoices settle exactly once between the two runs (3 = 30k×3 ≤ 100k).
    assert n1 + n2 == 3, f"each invoice settled once across both runs (got {n1}+{n2})"
    assert paid == 3
    # Balance debited by EXACTLY the settled total, never double — 100k - 90k = 10k.
    assert bal == Decimal("10000"), f"balance must be debited once per invoice, got {bal}"
    # One ledger txn per settled invoice — no double-settlement rows.
    assert txns == 3, f"exactly one balance txn per settled invoice, got {txns}"


async def test_settle_skips_already_paid_candidate_under_lock(migrated_db) -> None:
    """If an invoice in the locked candidate set is already paid (concurrent settler),
    the under-lock re-check skips it: it is neither re-counted nor re-debited."""
    from app.models import Tenant
    from app.models.enums import InvoiceStatus
    from app.services.billing import settle_invoices_from_balance

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="f22-paid", name="F22P", plan_code="lite", balance=Decimal("100000"))
        s.add(t)
        await s.flush()
        tid = t.id
        i1 = _inv(tid, 1, 40000)
        i2 = _inv(tid, 2, 40000)
        # i1 is ALREADY paid (a prior/concurrent settlement). It must be ignored.
        i1.status = InvoiceStatus.paid
        i1.paid_at = datetime.now(UTC)
        s.add_all([i1, i2])
        await s.commit()

        n = await settle_invoices_from_balance(s, t)
        await s.commit()

    # Only the open invoice (i2) settles; the already-paid i1 is not re-settled.
    assert n == 1
    async with Session() as s:
        bal = (await s.execute(select(Tenant.balance).where(Tenant.id == tid))).scalar_one()
    assert bal == Decimal("60000"), f"only i2 (40k) debited, got {bal}"
