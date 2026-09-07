"""#119 — reschedule must not corrupt paid history; new schedule settles exactly.

The bug: the reschedule endpoint lowered ``monthly_amount`` (remaining balance over
remaining months) but left ``principal`` unchanged and never called
``recompute_state``. Because ``recompute_state`` derives ``paid_count`` from
``floor(paid_amount / monthly_amount)`` — valid only for a *uniform* schedule —
the next payment recomputed the count off the smaller monthly and it jumped with
no money paid. The fix re-anchors progress to the amount/count paid at reschedule.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.enums import InstallmentStatus
from app.services.installments import apply_payment, recompute_state, reschedule

NOW = datetime(2026, 6, 15, tzinfo=UTC)


class _Inst:
    def __init__(self, **kw):
        self.start_date = date(2026, 1, 10)
        self.months = 12
        self.monthly_amount = Decimal("100000")
        self.principal = Decimal("1200000")
        self.paid_amount = Decimal("0")
        self.paid_count = 0
        self.next_due_date = None
        self.status = InstallmentStatus.active
        self.closed_at = None
        self.payoff_discount_amount = None
        self.resched_base_paid = None
        self.resched_base_count = None
        self.resched_base_due_date = None
        self.__dict__.update(kw)

    @property
    def remaining_balance(self) -> Decimal:
        """Mock the Installment.remaining_balance hybrid property."""
        return (self.principal or Decimal("0")) - (self.paid_amount or Decimal("0"))


def test_reschedule_keeps_paid_count_and_holds_invariant() -> None:
    inst = _Inst(paid_amount=Decimal("300000"), paid_count=3)
    recompute_state(inst, now=NOW)
    assert inst.paid_count == 3
    reschedule(inst, add_months=6, now=NOW)
    assert inst.months == 18
    assert inst.paid_count == 3  # history preserved
    remaining = inst.months - inst.paid_count  # 15
    # paid + monthly*remaining == principal → the new schedule settles exactly
    assert inst.paid_amount + inst.monthly_amount * remaining == inst.principal


def test_reschedule_then_paying_remaining_closes_exactly() -> None:
    inst = _Inst(paid_amount=Decimal("300000"), paid_count=3)
    recompute_state(inst, now=NOW)
    reschedule(inst, add_months=6, now=NOW)
    remaining = inst.months - inst.paid_count
    for _ in range(remaining):
        apply_payment(inst, inst.monthly_amount, now=NOW)
    assert inst.status == InstallmentStatus.completed
    assert inst.paid_amount >= inst.principal
    assert inst.next_due_date is None


def test_reschedule_recompute_idempotent_on_paid_count() -> None:
    # The core bug: the next recompute (any apply_payment site) must NOT advance
    # paid_count with no new money.
    inst = _Inst(paid_amount=Decimal("300000"), paid_count=3)
    recompute_state(inst, now=NOW)
    reschedule(inst, add_months=6, now=NOW)
    recompute_state(inst, now=NOW)  # e.g. a later apply_payment(0)
    assert inst.paid_count == 3


def test_reschedule_extreme_monthly_drop_preserves_count() -> None:
    # floor(paid/monthly) overcount regression: paid 3×1000, then spread the rest
    # thin (monthly 1000→200). floor(3000/200)=15 without re-anchoring.
    inst = _Inst(
        months=4, monthly_amount=Decimal("1000"), principal=Decimal("4000"),
        paid_amount=Decimal("3000"), paid_count=3,
    )
    recompute_state(inst, now=NOW)
    assert inst.paid_count == 3
    reschedule(inst, add_months=4, now=NOW)  # months 4→8, remaining 5
    assert inst.paid_count == 3
    # and it still closes exactly
    for _ in range(inst.months - inst.paid_count):
        apply_payment(inst, inst.monthly_amount, now=NOW)
    assert inst.status == InstallmentStatus.completed


def test_reschedule_next_due_date_override_wins() -> None:
    inst = _Inst(paid_amount=Decimal("300000"), paid_count=3, status=InstallmentStatus.overdue)
    reschedule(inst, add_months=0, next_due_date=date(2026, 9, 1), now=NOW)
    assert inst.next_due_date == date(2026, 9, 1)
    assert inst.status == InstallmentStatus.active  # pushed into the future


def test_reschedule_due_date_survives_the_next_payment() -> None:
    """Paying after a negotiated deferral advances from that agreement date.

    It must not snap back to the original contract calendar and immediately make
    the customer overdue again.
    """
    inst = _Inst(paid_amount=Decimal("300000"), paid_count=3)
    deferred_due = date(2026, 9, 1)
    reschedule(inst, next_due_date=deferred_due, now=NOW)

    apply_payment(inst, inst.monthly_amount, now=NOW)

    assert inst.paid_count == 4
    assert inst.next_due_date == date(2026, 10, 1)
    assert inst.status == InstallmentStatus.active
