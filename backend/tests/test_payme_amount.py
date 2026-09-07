"""#117 — Payme must validate the charged amount against the installment.

Payme amounts are in tiyin (1 UZS = 100 tiyin). An acceptable amount is strictly
positive and does not exceed the outstanding balance (principal − payoff discount
− already paid). The Payme handlers previously applied any amount, diverging from
the Click path and the Payme Merchant API contract (reject with -31001).
"""
from __future__ import annotations

from decimal import Decimal

from app.api.webhooks.payme import _amount_acceptable
from app.models import Installment


def _inst(principal="1000000", paid="0", discount=None) -> Installment:
    return Installment(
        principal=Decimal(principal),
        paid_amount=Decimal(paid),
        payoff_discount_amount=Decimal(discount) if discount is not None else None,
    )


def test_amount_equal_to_outstanding_is_accepted() -> None:
    assert _amount_acceptable(_inst(principal="1000000", paid="0"), 1000000_00) is True


def test_amount_within_outstanding_is_accepted() -> None:
    assert _amount_acceptable(_inst(principal="1000000", paid="0"), 100000_00) is True


def test_amount_exceeding_outstanding_is_rejected() -> None:
    # 600k already paid → only 400k owed; paying 500k must be rejected.
    assert _amount_acceptable(_inst(principal="1000000", paid="600000"), 500000_00) is False


def test_zero_or_negative_amount_rejected() -> None:
    assert _amount_acceptable(_inst(), 0) is False
    assert _amount_acceptable(_inst(), -1) is False


def test_fully_paid_rejects_any_amount() -> None:
    assert _amount_acceptable(_inst(principal="1000000", paid="1000000"), 1) is False


def test_payoff_discount_lowers_the_outstanding_ceiling() -> None:
    # 1,000,000 principal − 100,000 payoff discount → 900,000 owed.
    assert _amount_acceptable(_inst(principal="1000000", discount="100000"), 900000_00) is True
    assert _amount_acceptable(_inst(principal="1000000", discount="100000"), 950000_00) is False
