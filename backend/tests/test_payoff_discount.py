"""(#120) The early-payoff discount must apply only to the payoff payment. A
regular payment that doesn't settle the loan clears the stale discount, so
later payments can't accumulate past the discounted target and close the loan
below principal."""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.models import Installment
from app.models.enums import InstallmentStatus
from app.services.installments import apply_payment

NOW = datetime(2026, 6, 1, tzinfo=UTC)


def _inst(**kw) -> Installment:
    base = dict(
        principal=Decimal("1000"), paid_amount=Decimal("0"),
        monthly_amount=Decimal("100"), months=10,
        start_date=date(2026, 1, 1), status=InstallmentStatus.active,
        payoff_discount_amount=None,
    )
    base.update(kw)
    return Installment(**base)


def test_regular_payment_clears_a_stale_payoff_discount() -> None:
    # Payoff initiated (discount 100 → target 900) but a regular 100 is paid (→600).
    inst = _inst(paid_amount=Decimal("500"), payoff_discount_amount=Decimal("100"))
    apply_payment(inst, Decimal("100"), now=NOW)
    assert inst.status != InstallmentStatus.completed
    assert inst.payoff_discount_amount is None   # cleared (#120)


def test_actual_payoff_payment_settles_the_loan() -> None:
    # paid 500, discount 100 → payoff_amount = 1000-500-100 = 400 → paid hits target 900.
    inst = _inst(paid_amount=Decimal("500"), payoff_discount_amount=Decimal("100"))
    apply_payment(inst, Decimal("400"), now=NOW)
    assert inst.status == InstallmentStatus.completed


def test_regular_payments_cannot_close_the_loan_below_principal() -> None:
    # Without the fix, 4×100 from paid=500 reaches 900 == discounted target → wrongly
    # settles below the 1000 principal. With the fix the first payment voids the
    # discount, so the target reverts to full principal.
    inst = _inst(paid_amount=Decimal("500"), payoff_discount_amount=Decimal("100"))
    for _ in range(4):
        apply_payment(inst, Decimal("100"), now=NOW)
    assert inst.paid_amount == Decimal("900")
    assert inst.status != InstallmentStatus.completed
