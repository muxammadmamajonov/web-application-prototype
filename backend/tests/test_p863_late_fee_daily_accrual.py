"""(#863) The daily late-fee cron must accrue ONE day at a time — the gap since the last
fee — not the full days-since-due on every run. Charging ``today - next_due_date`` daily
compounded the fee quadratically (day N charged N×, on top of every prior day), massively
overcharging customers.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.business_time import tashkent_today


def test_late_fee_days_counts_since_last_fee_not_since_due() -> None:
    from app.services.scheduler import _late_fee_days

    today = date(2026, 6, 15)
    # A fee was charged yesterday → today covers exactly 1 day, no matter how long overdue.
    assert _late_fee_days(today=today, last_fee_date=today - timedelta(days=1),
                          next_due_date=today - timedelta(days=30)) == 1
    # First fee (no prior late-fee row) → one-time catch-up from the due date.
    assert _late_fee_days(today=today, last_fee_date=None,
                          next_due_date=today - timedelta(days=5)) == 5
    # A missed cron day → the next run catches up exactly the elapsed days.
    assert _late_fee_days(today=today, last_fee_date=today - timedelta(days=3),
                          next_due_date=today - timedelta(days=30)) == 3
    # Defensive: no anchors at all → at least 1.
    assert _late_fee_days(today=today, last_fee_date=None, next_due_date=None) == 1


async def test_apply_late_fees_charges_one_day_after_a_prior_fee(migrated_db, monkeypatch) -> None:
    from app.core.money_utils import round_amount
    from app.models import Customer, Installment, Payment, Tenant
    from app.models.enums import InstallmentStatus, PaymentMethod, PaymentStatus
    from app.services import scheduler
    from app.services.scheduler import tashkent_today

    monkeypatch.setattr(scheduler.settings, "enable_late_fees", True)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    today = tashkent_today()
    principal = Decimal("1000000")
    rate = Decimal("0.001")   # 0.1% per day

    async with Session() as s:
        t = Tenant(slug="p863-t", name="P863")
        s.add(t)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="Late Payer", phone="+998900000863")
        s.add(c)
        await s.flush()
        inst = Installment(currency="UZS",
            tenant_id=t.id, customer_id=c.id,
            total_amount=principal, principal=principal, down_payment=Decimal("0"),
            months=12, monthly_amount=Decimal("100000"), paid_amount=Decimal("0"),
            start_date=today - timedelta(days=60), status=InstallmentStatus.overdue,
            next_due_date=today - timedelta(days=30), late_fee_pct_per_day=rate,
        )
        s.add(inst)
        await s.flush()
        iid = inst.id
        # A late fee was ALREADY charged yesterday → today must cover exactly ONE day.
        s.add(Payment(currency="UZS",
            tenant_id=t.id, installment_id=iid, customer_id=c.id,
            amount=round_amount(rate * principal), method=PaymentMethod.late_fee,
            status=PaymentStatus.approved, fee_date=today - timedelta(days=1),
            paid_at=datetime.now(UTC), approved_at=datetime.now(UTC),
        ))
        await s.commit()

    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    result = await scheduler.apply_late_fees_job()
    assert result["applied"] >= 1, f"a fee should be applied, got {result}"

    async with Session() as s:
        todays_fee = (await s.execute(
            select(Payment.amount).where(
                Payment.installment_id == iid,
                Payment.method == PaymentMethod.late_fee,
                Payment.fee_date == today,
            )
        )).scalar_one()
    expected = round_amount(rate * Decimal(1) * principal)   # one day, not 30
    assert todays_fee == expected, (
        f"today's fee must cover ONE day since yesterday's fee (not the 30 days overdue): "
        f"got {todays_fee}, expected {expected}"
    )
