"""(#861) Settling all of a tenant's outstanding invoices from balance reactivates a
tenant that dunning suspended/blocked for non-payment.

run_dunning only does the forward transitions (open→overdue→suspended→blocked) and nothing
else reactivates on payment, so without this fix a paying customer who clears their dues via
a top-up stays locked out. Partial settlement must NOT reactivate.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


def _overdue_invoice(tenant_id, amount: int, *, number: str, month: int = 1):
    from app.models import Invoice
    from app.models.enums import InvoiceStatus

    now = datetime.now(UTC)
    # Distinct period per invoice — there's a unique (tenant_id, period_start) constraint.
    return Invoice(
        tenant_id=tenant_id, plan_code="start", invoice_number=number,
        period_start=date(2026, month, 1), period_end=date(2026, month, 28),
        amount=Decimal(amount), status=InvoiceStatus.overdue,
        issued_at=now - timedelta(days=20), due_at=now - timedelta(days=10),
    )


async def _make_tenant(Session, *, slug: str, status, balance: int):
    from app.models import Tenant
    from app.models.enums import TenantStatus

    async with Session() as s:
        t = Tenant(slug=slug, name="P861", plan_code="start", status=status,
                   balance=Decimal(balance))
        if status in (TenantStatus.suspended, TenantStatus.blocked):
            t.suspended_at = datetime.now(UTC) - timedelta(days=7)
        s.add(t)
        await s.flush()
        tid = t.id
        await s.commit()
    return tid


async def _settle(Session, tid) -> int:
    from app.models import Tenant
    from app.services.billing import settle_invoices_from_balance

    async with Session() as s:
        # The real callers lock the tenant FOR UPDATE before settling.
        tenant = (await s.execute(
            select(Tenant).where(Tenant.id == tid).with_for_update()
        )).scalar_one()
        n = await settle_invoices_from_balance(s, tenant)
        await s.commit()
    return n


async def _status(Session, tid):
    from app.models import Tenant

    async with Session() as s:
        return (await s.execute(
            select(Tenant.status, Tenant.suspended_at).where(Tenant.id == tid)
        )).one()


async def test_settle_reactivates_suspended_when_all_dues_cleared(migrated_db) -> None:
    from app.models.enums import TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid = await _make_tenant(Session, slug="p861-susp", status=TenantStatus.suspended, balance=100)
    async with Session() as s:
        s.add(_overdue_invoice(tid, 60, number="P861-A"))
        await s.commit()

    assert await _settle(Session, tid) == 1
    status, suspended_at = await _status(Session, tid)
    assert status == TenantStatus.active, f"a suspended tenant who clears all dues must reactivate, got {status}"
    assert suspended_at is None, "suspended_at must be cleared on reactivation"


async def test_settle_reactivates_blocked_when_all_dues_cleared(migrated_db) -> None:
    from app.models.enums import TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid = await _make_tenant(Session, slug="p861-blk", status=TenantStatus.blocked, balance=100)
    async with Session() as s:
        s.add(_overdue_invoice(tid, 60, number="P861-C"))
        await s.commit()

    assert await _settle(Session, tid) == 1
    status, suspended_at = await _status(Session, tid)
    assert status == TenantStatus.active, f"a blocked tenant who clears all dues must reactivate, got {status}"
    assert suspended_at is None


async def test_settle_partial_payment_does_not_reactivate(migrated_db) -> None:
    from app.models.enums import TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    # Balance covers exactly ONE of two 60-so'm invoices → still delinquent.
    tid = await _make_tenant(Session, slug="p861-part", status=TenantStatus.suspended, balance=60)
    async with Session() as s:
        s.add(_overdue_invoice(tid, 60, number="P861-B1", month=1))
        s.add(_overdue_invoice(tid, 60, number="P861-B2", month=2))
        await s.commit()

    assert await _settle(Session, tid) == 1, "only one invoice should be settled"
    status, _ = await _status(Session, tid)
    assert status == TenantStatus.suspended, f"partial payment must NOT reactivate, got {status}"


async def test_settle_leaves_active_tenant_active(migrated_db) -> None:
    from app.models.enums import TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid = await _make_tenant(Session, slug="p861-act", status=TenantStatus.active, balance=100)
    async with Session() as s:
        s.add(_overdue_invoice(tid, 60, number="P861-D"))
        await s.commit()

    assert await _settle(Session, tid) == 1
    status, _ = await _status(Session, tid)
    assert status == TenantStatus.active, f"an active tenant must stay active, got {status}"
