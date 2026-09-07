"""C1 — installment markup/interest calculation."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.calc import compute_schedule


def _args(**over):
    base = dict(
        goods_total=Decimal("1200000"), down_payment=Decimal("0"),
        months=12, formula="none", markup_pct=None, interest_rate=None,
    )
    base.update(over)
    return base


def test_none_is_interest_free() -> None:
    r = compute_schedule(**_args(formula="none"))
    assert r.monthly_amount == Decimal("100000")
    assert r.principal == Decimal("1200000")
    assert r.total_amount == Decimal("1200000")


def test_markup_applies_percentage() -> None:
    # 20% markup on 1,200,000 financed => repay 1,440,000 over 12 = 120,000/mo.
    r = compute_schedule(**_args(formula="markup", markup_pct=Decimal("20")))
    assert r.monthly_amount == Decimal("120000")
    assert r.principal == Decimal("1440000")
    # total_amount stays the cash price; markup shows up in principal/monthly.
    assert r.total_amount == Decimal("1200000")


def test_markup_respects_down_payment() -> None:
    # Finance 1,000,000 after 200k down, +10% => 1,100,000 over 10 = 110,000/mo.
    r = compute_schedule(**_args(
        goods_total=Decimal("1200000"), down_payment=Decimal("200000"),
        months=10, formula="markup", markup_pct=Decimal("10"),
    ))
    assert r.financed == Decimal("1000000")
    assert r.monthly_amount == Decimal("110000")
    assert r.principal == Decimal("1100000")


def test_principal_equals_monthly_times_months() -> None:
    # No rounding drift: a full run of monthly payments closes the contract.
    r = compute_schedule(**_args(formula="markup", markup_pct=Decimal("7"), months=7))
    assert r.principal == r.monthly_amount * r.months


def test_annuity_costs_more_than_principal() -> None:
    r = compute_schedule(**_args(formula="annuity", interest_rate=Decimal("0.03"), months=12))
    assert r.principal > Decimal("1200000")  # interest added
    assert r.monthly_amount * r.months == r.principal


def test_annuity_zero_rate_falls_back_to_none() -> None:
    r = compute_schedule(**_args(formula="annuity", interest_rate=Decimal("0")))
    assert r.principal == Decimal("1200000")


def test_differentiated_total_interest_formula() -> None:
    # total interest = P·r·(n+1)/2 = 1,200,000·0.02·(13)/2 = 156,000 => 1,356,000.
    r = compute_schedule(**_args(formula="differentiated", interest_rate=Decimal("0.02"), months=12))
    assert r.principal == Decimal("1356000")


def test_down_payment_too_high_raises() -> None:
    with pytest.raises(ValueError, match="down_payment_too_high"):
        compute_schedule(**_args(goods_total=Decimal("500000"), down_payment=Decimal("500000")))


def test_unknown_formula_treated_as_none() -> None:
    r = compute_schedule(**_args(formula="bogus", markup_pct=Decimal("50")))
    assert r.principal == Decimal("1200000")  # markup ignored for unknown formula


def test_negative_interest_rate_rejected() -> None:
    """A negative interest rate is invalid: rejected loudly (for both interest
    formulas) rather than silently falling through to an interest-free, margin-losing
    schedule. (The rate=-2 case also can't reach the annuity denominator — the
    formula requires rate > 0 — so this guard is what actually protects the math.)"""
    with pytest.raises(ValueError, match="interest_rate_must_be_non_negative"):
        compute_schedule(**_args(formula="annuity", interest_rate=Decimal("-2"), months=2))
    with pytest.raises(ValueError, match="interest_rate_must_be_non_negative"):
        compute_schedule(**_args(formula="differentiated", interest_rate=Decimal("-0.5"), months=12))


def test_negative_markup_rejected() -> None:
    """A negative markup is invalid — the same margin-loss guard as the interest rate."""
    with pytest.raises(ValueError, match="markup_must_be_non_negative"):
        compute_schedule(**_args(formula="markup", markup_pct=Decimal("-10")))


def test_repayable_always_positive() -> None:
    """A valid (positive-rate) configuration yields a positive repayable total +
    monthly amount, with no rounding drift (principal == monthly * months)."""
    r = compute_schedule(**_args(formula="annuity", interest_rate=Decimal("0.5"), months=12))
    assert r.principal > 0
    assert r.monthly_amount > 0
    assert r.monthly_amount * r.months == r.principal
