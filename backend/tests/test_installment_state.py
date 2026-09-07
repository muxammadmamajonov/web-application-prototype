"""H5 — schedule state derived from paid_amount (no per-event drift)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.enums import InstallmentStatus
from app.services.installments import add_months, apply_payment, recompute_state, reverse_payment

NOW = datetime(2026, 6, 15, tzinfo=UTC)


class _Inst:
    """Stand-in with just the attributes the state helper touches."""

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
        self.__dict__.update(kw)


def test_add_months_clamps_day() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 15), 2) == date(2027, 1, 15)


def test_one_monthly_payment_advances_one_month() -> None:
    inst = _Inst()
    apply_payment(inst, Decimal("100000"), now=NOW)
    assert inst.paid_count == 1
    assert inst.next_due_date == date(2026, 3, 10)  # 2nd month due


def test_partial_payment_does_not_advance_a_full_month() -> None:
    inst = _Inst()
    apply_payment(inst, Decimal("50000"), now=NOW)  # half a month
    assert inst.paid_count == 0
    assert inst.next_due_date == date(2026, 2, 10)  # still the 1st month


def test_overpayment_advances_multiple_months() -> None:
    inst = _Inst()
    apply_payment(inst, Decimal("250000"), now=NOW)  # 2.5 months
    assert inst.paid_count == 2
    assert inst.next_due_date == date(2026, 4, 10)


def test_full_payment_completes() -> None:
    inst = _Inst()
    apply_payment(inst, Decimal("1200000"), now=NOW)
    assert inst.status == InstallmentStatus.completed
    assert inst.next_due_date is None
    assert inst.closed_at == NOW


def test_reversal_reopens_and_recomputes() -> None:
    inst = _Inst(paid_amount=Decimal("1200000"), status=InstallmentStatus.completed, closed_at=NOW)
    recompute_state(inst, now=NOW)  # confirm closed
    reverse_payment(inst, Decimal("1200000"), now=NOW)
    assert inst.status != InstallmentStatus.completed
    assert inst.closed_at is None
    assert inst.paid_count == 0


def test_behind_schedule_marks_overdue() -> None:
    # 1 month paid, but we're at month 6 → next due (month 2) is in the past.
    inst = _Inst()
    apply_payment(inst, Decimal("100000"), now=NOW)
    assert inst.status == InstallmentStatus.overdue


# ── #108 early-payoff discount settles the loan ────────────────────────────

def test_early_payoff_discount_completes_below_principal() -> None:
    # Loan of 1,200,000 with a 10% early-payoff discount (120,000). Paying the
    # discounted payoff (1,080,000 — below principal) MUST close the loan, not
    # leave a 120,000 residual that keeps dunning the customer.
    inst = _Inst(payoff_discount_amount=Decimal("120000"))
    apply_payment(inst, Decimal("1080000"), now=NOW)
    assert inst.status == InstallmentStatus.completed
    assert inst.next_due_date is None
    assert inst.closed_at == NOW
    assert inst.paid_count == inst.months  # shown as fully paid


def test_without_discount_paying_below_principal_does_not_complete() -> None:
    # Same under-principal amount but NO discount recorded → still owing.
    inst = _Inst()
    apply_payment(inst, Decimal("1080000"), now=NOW)
    assert inst.status != InstallmentStatus.completed
    assert inst.closed_at is None


def test_partial_payment_with_discount_still_not_settled() -> None:
    # A discount is recorded, but the customer pays less than the payoff target
    # (1,080,000) → loan stays open.
    inst = _Inst(payoff_discount_amount=Decimal("120000"))
    apply_payment(inst, Decimal("500000"), now=NOW)
    assert inst.status != InstallmentStatus.completed
