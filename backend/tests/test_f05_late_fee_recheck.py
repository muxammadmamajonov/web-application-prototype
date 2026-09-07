"""(F-05) The late-fee cron must lock each overdue installment and RE-CHECK it's
still overdue & unpaid *under the lock* before charging a fee — otherwise a
concurrent webhook that pays the loan off between the cron's (unlocked) snapshot
and its INSERT gets charged a late fee on a just-completed loan.

DB-backed (self-skips without TEST_DATABASE_URL via ``migrated_db``).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.business_time import tashkent_today


async def _seed_overdue(Session, *, slug: str, paid: Decimal = Decimal("0")):
    from app.models import Customer, Installment, Tenant
    from app.models.enums import InstallmentStatus
    from app.services.scheduler import tashkent_today

    today = tashkent_today()
    principal = Decimal("1000000")
    async with Session() as s:
        t = Tenant(slug=slug, name=slug)
        s.add(t)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="Payer", phone="+998900000505")
        s.add(c)
        await s.flush()
        inst = Installment(currency="UZS",
            tenant_id=t.id, customer_id=c.id,
            total_amount=principal, principal=principal, down_payment=Decimal("0"),
            months=12, monthly_amount=Decimal("100000"), paid_amount=paid,
            start_date=today - timedelta(days=60), status=InstallmentStatus.overdue,
            next_due_date=today - timedelta(days=30), late_fee_pct_per_day=Decimal("0.001"),
        )
        s.add(inst)
        await s.flush()
        iid = inst.id
        await s.commit()
    return iid


async def _todays_fee_count(Session, iid) -> int:
    from app.models import Payment
    from app.models.enums import PaymentMethod
    from app.services.scheduler import tashkent_today

    today = tashkent_today()
    async with Session() as s:
        return (await s.execute(
            select(func.count(Payment.id)).where(
                Payment.installment_id == iid,
                Payment.method == PaymentMethod.late_fee,
                Payment.fee_date == today,
            )
        )).scalar_one()


async def test_no_fee_when_loan_completed_under_lock(migrated_db, monkeypatch) -> None:
    """The exact F-05 interleave, made deterministic with events: a concurrent webhook
    holds the installment row lock FOR UPDATE while the late-fee job takes its (unlocked)
    overdue snapshot and then blocks on the same row's lock; the webhook then applies the
    final payment, flips the loan to completed, and commits. When the job's FOR UPDATE
    unblocks it must RE-CHECK and charge NOTHING on the now-completed loan."""
    from app.models import Installment
    from app.models.enums import InstallmentStatus
    from app.services import scheduler

    monkeypatch.setattr(scheduler.settings, "enable_late_fees", True)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    iid = await _seed_overdue(Session, slug="f05-race")

    lock_held = asyncio.Event()   # set once the payoff holds the row lock
    cron_may_commit = asyncio.Event()  # set once the cron has had time to reach FOR UPDATE

    async def _payoff():
        async with Session() as s:
            inst = (await s.execute(
                select(Installment).where(Installment.id == iid).with_for_update()
            )).scalar_one()
            lock_held.set()
            # Hold the lock until the cron is parked on it, then pay off & complete.
            await cron_may_commit.wait()
            inst.paid_amount = inst.principal
            inst.status = InstallmentStatus.completed
            await s.commit()

    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    payoff = asyncio.create_task(_payoff())
    await lock_held.wait()  # the row is now locked by the "webhook"

    # Launch the cron; it snapshots (sees overdue+unpaid) then blocks on the FOR UPDATE.
    cron = asyncio.create_task(scheduler.apply_late_fees_job())
    # Give the cron a beat to reach and park on the lock, then let the payoff commit.
    await asyncio.sleep(0.2)
    cron_may_commit.set()
    await payoff
    result = await cron

    assert await _todays_fee_count(Session, iid) == 0, (
        "no late fee may be charged on a loan that was paid off / completed under the lock"
    )
    async with Session() as s:
        st = (await s.execute(
            select(Installment.status).where(Installment.id == iid)
        )).scalar_one()
    assert st == InstallmentStatus.completed
    # The job observed the cure under the lock and counted it as skipped_paid.
    assert result.get("skipped_paid", 0) >= 1, f"expected a skipped_paid, got {result}"


async def test_fee_charged_for_genuinely_overdue_loan(migrated_db, monkeypatch) -> None:
    """Control: with no concurrent payoff, a genuinely overdue+unpaid loan still gets
    its fee — the re-check must not block the normal path."""
    from app.services import scheduler

    monkeypatch.setattr(scheduler.settings, "enable_late_fees", True)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    iid = await _seed_overdue(Session, slug="f05-normal")

    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    result = await scheduler.apply_late_fees_job()

    assert await _todays_fee_count(Session, iid) == 1, f"a fee should be charged, got {result}"
    assert result["applied"] >= 1
