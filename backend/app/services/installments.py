"""Installment state transitions kept in one place.

Every payment-application site (Click/Payme webhooks, manual cash approval,
refunds) used to bump ``paid_count`` by one and roll ``next_due_date`` forward
exactly 30 days regardless of how much was actually paid. That drifted the
schedule (over/under-payment advanced a full month; 30-day hops desynced from
calendar months; cash approvals didn't advance the due date at all).

``apply_payment`` / ``reverse_payment`` mutate ``paid_amount`` and then
``recompute_state`` derives everything else from it, so the schedule is always
consistent with the money actually collected.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal

from app.core.money_utils import round_som
from app.models import Installment
from app.models.enums import InstallmentStatus


def add_months(d: date, n: int) -> date:
    """Add ``n`` calendar months, clamping the day to the target month length."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def accepts_payment(inst: Installment) -> bool:
    """Whether new money may be applied to this contract.

    Completed and cancelled contracts are terminal for new payments. Refunds
    use ``reverse_payment`` and remain able to reopen a completed contract.
    """
    return inst.status not in (InstallmentStatus.completed, InstallmentStatus.cancelled)


def recompute_state(inst: Installment, *, now: datetime) -> None:
    """Derive ``paid_count`` / ``next_due_date`` / ``status`` / ``closed_at``
    from ``paid_amount``. Idempotent; safe to call after any balance change."""
    monthly = inst.monthly_amount or Decimal("0")
    principal = inst.principal or Decimal("0")
    paid = inst.paid_amount or Decimal("0")
    months = inst.months or 0
    # (#108) An early-payoff discount lowers the amount needed to settle the loan.
    # The loan is "paid off" at ``principal - payoff_discount_amount``; without
    # this, paying the discounted quote leaves a residual balance == the discount
    # and the customer keeps getting overdue reminders + late fees.
    discount = inst.payoff_discount_amount or Decimal("0")
    payoff_target = principal - discount

    settled = principal > 0 and paid >= payoff_target

    # (#119) A reschedule changes ``monthly_amount`` mid-loan, so the already-paid
    # installments cannot be recovered with ``floor(paid / monthly)`` (it would
    # over-count them at the new, smaller monthly). When a reschedule baseline is
    # recorded, count future progress from the amount/count settled at that point.
    base_paid = getattr(inst, "resched_base_paid", None)
    base_count = getattr(inst, "resched_base_count", 0) or 0
    if settled:
        months_paid = months
    elif monthly > 0 and base_paid is not None:
        extra = paid - base_paid
        months_paid = base_count + (
            int(extra // monthly) if extra > 0 else 0
        )
    elif monthly > 0:
        months_paid = int(paid // monthly)
    else:
        months_paid = 0
    months_paid = max(0, min(months_paid, months))
    inst.paid_count = months_paid

    if settled:
        inst.status = InstallmentStatus.completed
        inst.closed_at = now
        inst.next_due_date = None
        return

    # Re-open a previously-closed contract (e.g. a refund reversed it).
    if inst.status == InstallmentStatus.completed:
        inst.closed_at = None

    resched_due = getattr(inst, "resched_base_due_date", None)
    if base_paid is not None and resched_due is not None:
        # The anchor is the first due date after the reschedule baseline.
        inst.next_due_date = add_months(resched_due, months_paid - base_count)
    else:
        base = inst.start_date or now.date()
        inst.next_due_date = add_months(base, months_paid + 1)
    # The payment may have cleared arrears (→ active) or left some (→ overdue).
    inst.status = (
        InstallmentStatus.overdue if inst.next_due_date < now.date()
        else InstallmentStatus.active
    )


def overpayment_excess(inst: Installment, amount: Decimal) -> Decimal:
    """How far ``amount`` would overshoot settling this contract; 0 if it fits.

    (QA H-1) Nothing used to compare a payment against the balance, so a typo'd
    extra zero booked a 10x payment, marked the loan complete and corrupted
    every revenue figure derived from it — with no refund trail to unwind it.

    The target is the same one `recompute_state` settles against,
    ``principal - payoff_discount_amount``, so an exact early-payoff quote is
    accepted rather than treated as an overpayment.

    THE ROUNDING ALLOWANCE is the subtle part. ``monthly_amount`` is quantized
    ROUND_HALF_UP, so paying the stated monthly ``months`` times can legitimately
    land a little ABOVE principal — 10,000,000 over 6 months bills 1,666,667 x 6
    = 10,000,002. A strict ``amount > principal - paid`` test would refuse the
    final instalment of most contracts and trap customers one payment short of
    closing their loan, while the app was showing them that exact amount to pay.
    The drift is under one currency unit per instalment, so ``months`` units
    bounds it exactly — tight enough that a real overpayment (the 45,000,000 in
    the report, or even 35,000) still fails.
    """
    principal = inst.principal or Decimal("0")
    discount = inst.payoff_discount_amount or Decimal("0")
    paid = inst.paid_amount or Decimal("0")
    shortfall = principal - discount - paid
    rounding_slack = Decimal(inst.months or 0)
    return max(Decimal("0"), (amount or Decimal("0")) - shortfall - rounding_slack)


def apply_payment(inst: Installment, amount: Decimal, *, now: datetime) -> None:
    inst.paid_amount = (inst.paid_amount or Decimal("0")) + (amount or Decimal("0"))
    recompute_state(inst, now=now)
    # (#120) The early-payoff discount applies only to the payoff payment, which
    # settles the loan in one shot (paid reaches principal - discount). If this
    # payment did NOT settle the loan, the payoff wasn't completed — drop the
    # discount so it can't linger and let later regular payments accumulate past
    # the lowered target and settle the loan below principal.
    if inst.status != InstallmentStatus.completed and inst.payoff_discount_amount:
        inst.payoff_discount_amount = None


def reverse_payment(inst: Installment, amount: Decimal, *, now: datetime) -> None:
    was_cancelled = inst.status == InstallmentStatus.cancelled
    cancelled_at = inst.closed_at
    inst.paid_amount = max(Decimal("0"), (inst.paid_amount or Decimal("0")) - (amount or Decimal("0")))
    recompute_state(inst, now=now)
    # A provider refund changes money, not the operator's terminal cancellation.
    if was_cancelled:
        inst.status = InstallmentStatus.cancelled
        inst.closed_at = cancelled_at


def reschedule(
    inst: Installment,
    *,
    add_months: int = 0,
    next_due_date: date | None = None,
    now: datetime,
) -> None:
    """Restructure an open loan: extend the term and/or push the next due date.

    ``monthly_amount`` is re-derived over the remaining balance and remaining
    months. Crucially this **re-anchors paid progress** (#119): it records the
    amount/count already settled so ``recompute_state`` doesn't re-interpret the
    already-paid installments at the new (smaller) monthly — which previously
    jumped ``paid_count`` with no money paid. ``principal`` is re-derived so that
    paying the new schedule settles to exactly zero (rounding absorbed in total).
    """
    if add_months:
        inst.months = min(120, (inst.months or 0) + add_months)
    paid = inst.paid_amount or Decimal("0")
    paid_count = inst.paid_count or 0
    remaining_months = max(1, (inst.months or 1) - paid_count)
    remaining_principal = inst.remaining_balance
    new_monthly = (
        round_som(remaining_principal / remaining_months)
        if remaining_principal > 0
        else Decimal("0")
    )
    inst.monthly_amount = new_monthly
    inst.resched_base_paid = paid
    inst.resched_base_count = paid_count
    inst.principal = paid + new_monthly * remaining_months
    recompute_state(inst, now=now)
    # A manual next-due override wins over the derived date.
    if next_due_date is not None:
        inst.next_due_date = next_due_date
        if inst.status == InstallmentStatus.overdue and next_due_date >= now.date():
            inst.status = InstallmentStatus.active
    # Persist the first post-reschedule due date as the stable schedule anchor.
    # This must happen after recompute/override so a later payment advances from
    # the agreement rather than from the original contract start date.
    inst.resched_base_due_date = inst.next_due_date
