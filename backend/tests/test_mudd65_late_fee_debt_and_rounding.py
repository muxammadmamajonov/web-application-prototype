"""MUDD-65 (H4 + M-01) — a late fee must actually land on the customer's debt,
using the contract's own rounding granularity.

H4: ``_apply_late_fees_job_enabled`` computed a fee and wrote a ``late_fee``
Payment row, but never touched ``Installment.principal`` or ``paid_amount``.
``Installment.remaining_balance`` (``principal - paid_amount``) — the ONLY
thing ``total_outstanding``/``remaining`` read sites derive from — therefore
never moved: the fee was charged and permanently uncollectable.

M-01: the fee was rounded with ``core.money_utils.round_amount`` (2 decimal
places, the billing/subscription domain's granularity) while every contract
money field (``principal``, ``monthly_amount``) is whole so'm
(``services.calc._q`` / the old inline reschedule quantize). Adding a
2-decimal fee onto a whole-so'm ``principal`` would leave a fractional-so'm
residual balance nothing could ever fully collect.

DB-backed: self-skips without TEST_DATABASE_URL (the standard integration
gate).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.business_time import tashkent_today


async def _make_overdue_installment(Session, *, slug: str, rate: Decimal, principal: Decimal):
    from app.models import Customer, Installment, Tenant
    from app.models.enums import InstallmentStatus
    from app.services.scheduler import tashkent_today

    today = tashkent_today()
    async with Session() as s:
        t = Tenant(slug=slug, name="T")
        s.add(t)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="Overdue Buyer", phone=f"+998900{slug[-6:]}")
        s.add(c)
        await s.flush()
        inst = Installment(
            currency="UZS",
            tenant_id=t.id, customer_id=c.id,
            total_amount=principal, principal=principal, down_payment=Decimal("0"),
            months=12, monthly_amount=(principal / 12).quantize(Decimal("1")),
            paid_amount=Decimal("0"),
            start_date=today - timedelta(days=60), status=InstallmentStatus.overdue,
            next_due_date=today - timedelta(days=30), late_fee_pct_per_day=rate,
        )
        s.add(inst)
        await s.flush()
        iid = inst.id
        await s.commit()
    return iid


async def test_mudd65_late_fee_grows_remaining_balance(migrated_db, monkeypatch) -> None:
    """H4: applying a late fee must increase `principal` (and thus
    `remaining_balance`) by exactly the fee charged — not just log a Payment
    row nobody's debt reflects."""
    from app.models import Installment, Payment
    from app.models.enums import InstallmentStatus, PaymentMethod
    from app.services import scheduler

    monkeypatch.setattr(scheduler.settings, "enable_late_fees", True)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    rate = Decimal("0.001")  # 0.1% per day
    principal = Decimal("1000000")
    iid = await _make_overdue_installment(
        Session, slug="mudd65-h4", rate=rate, principal=principal,
    )

    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    result = await scheduler.apply_late_fees_job()
    assert result["applied"] >= 1, f"a fee should have applied, got {result}"

    async with Session() as s:
        inst = (await s.execute(
            select(Installment).where(Installment.id == iid)
        )).scalar_one()
        fee = (await s.execute(
            select(Payment.amount).where(
                Payment.installment_id == iid,
                Payment.method == PaymentMethod.late_fee,
            )
        )).scalar_one()

    assert fee > 0
    # The fee must be added to the debt, not just recorded.
    assert inst.principal == principal + fee, (
        f"principal must grow by the fee: expected {principal + fee}, got {inst.principal}"
    )
    assert inst.remaining_balance == inst.principal - inst.paid_amount
    assert inst.remaining_balance == principal + fee, (
        "the customer's remaining balance must reflect the fee — "
        f"expected {principal + fee}, got {inst.remaining_balance}"
    )
    # paid_amount must stay untouched — a fee is owed, not already paid.
    assert inst.paid_amount == Decimal("0")
    # recompute_state ran and left the loan consistent: still overdue (nothing
    # was paid), and the schedule position (paid_count/next_due_date) is
    # unaffected by a debt-only change.
    assert inst.status == InstallmentStatus.overdue
    assert inst.paid_count == 0


async def test_mudd65_late_fee_paying_it_off_closes_the_loan(migrated_db, monkeypatch) -> None:
    """H4, continued: once the fee is on `principal`, paying the full
    (original principal + fee) actually settles the loan — proving the fee is
    real, collectable debt, not a phantom charge that can never be paid off."""
    from app.models import Installment
    from app.models.enums import InstallmentStatus
    from app.services import scheduler
    from app.services.installments import apply_payment

    monkeypatch.setattr(scheduler.settings, "enable_late_fees", True)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    rate = Decimal("0.001")
    principal = Decimal("1000000")
    iid = await _make_overdue_installment(
        Session, slug="mudd65-payoff", rate=rate, principal=principal,
    )

    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    await scheduler.apply_late_fees_job()

    async with Session() as s:
        inst = (await s.execute(
            select(Installment).where(Installment.id == iid)
        )).scalar_one()
        total_owed = inst.principal  # original principal + accrued fee

        apply_payment(inst, total_owed, now=datetime.now(UTC))
        await s.commit()

        inst2 = (await s.execute(
            select(Installment).where(Installment.id == iid)
        )).scalar_one()
        assert inst2.status == InstallmentStatus.completed
        assert inst2.remaining_balance == Decimal("0")


async def test_mudd65_late_fee_rounds_to_whole_som_not_two_decimals(migrated_db, monkeypatch) -> None:
    """M-01: the fee must round the same way as the rest of the contract
    ledger (whole so'm), not `core.money_utils.round_amount`'s 2-decimal-place
    granularity used by the unrelated billing/subscription domain."""
    from app.core.money_utils import round_amount, round_som
    from app.models import Installment, Payment
    from app.models.enums import PaymentMethod, PaymentStatus
    from app.services import scheduler
    from app.services.scheduler import tashkent_today

    monkeypatch.setattr(scheduler.settings, "enable_late_fees", True)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    # `late_fee_pct_per_day` is Numeric(6,4) — at most 4 decimal places — and
    # chosen so rate * 1 day * remaining is NOT already a whole number:
    # 0.0007 * 1234567 = 864.1969, so the two roundings genuinely disagree.
    rate = Decimal("0.0007")
    principal = Decimal("1234567")
    today = tashkent_today()
    iid = await _make_overdue_installment(
        Session, slug="mudd65-m01", rate=rate, principal=principal,
    )
    # A fee already accrued yesterday, so `_late_fee_days` charges exactly ONE
    # day today (same anchoring as #863) — otherwise it catches up all 30 days
    # since `next_due_date`, making the expected raw amount below wrong.
    async with Session() as s:
        inst = (await s.execute(select(Installment).where(Installment.id == iid))).scalar_one()
        s.add(Payment(
            currency="UZS", tenant_id=inst.tenant_id, installment_id=iid,
            customer_id=inst.customer_id, amount=Decimal("1"),
            method=PaymentMethod.late_fee, status=PaymentStatus.approved,
            fee_date=today - timedelta(days=1),
            paid_at=datetime.now(UTC), approved_at=datetime.now(UTC),
        ))
        await s.commit()

    raw = rate * Decimal(1) * principal
    cents_rounded = round_amount(raw)      # the old (wrong) granularity
    som_rounded = round_som(raw)           # the contract-ledger granularity
    assert cents_rounded != som_rounded, "test setup must exercise a genuine rounding disagreement"

    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    result = await scheduler.apply_late_fees_job()
    assert result["applied"] >= 1, f"a fee should have applied, got {result}"

    async with Session() as s:
        fee = (await s.execute(
            select(Payment.amount).where(
                Payment.installment_id == iid,
                Payment.method == PaymentMethod.late_fee,
                Payment.fee_date == today,
            )
        )).scalar_one()
        inst = (await s.execute(
            select(Installment).where(Installment.id == iid)
        )).scalar_one()

    assert fee == som_rounded, f"fee must use whole-so'm rounding: got {fee}, expected {som_rounded}"
    assert fee == fee.to_integral_value(), f"fee must have no fractional so'm: got {fee}"
    assert inst.principal == inst.principal.to_integral_value(), (
        f"principal must stay whole so'm after the fee lands: got {inst.principal}"
    )
