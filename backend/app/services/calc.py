"""Installment schedule calculation.

Turns a cash price + financing terms into the repayable total and the flat
monthly amount. Until this existed, ``create_installment`` ignored the shop's
configured markup/interest entirely and every contract was effectively
interest-free — the shop's margin silently vanished (the C1 bug).

Four formulas (matching ``TenantSettings.default_calc_formula`` /
``CalcFormula`` in the API layer):

* ``none``            — no financing cost; repay exactly the financed amount.
* ``markup``          — flat percentage markup on the financed amount
                        (the dominant Uzbek "narx + ustama %" model).
* ``annuity``         — equal monthly payments at a monthly interest rate.
* ``differentiated``  — declining-balance interest; we charge the exact total
                        interest but expose a flat (averaged) monthly amount,
                        since the schema stores a single ``monthly_amount``.

Money convention used across the app: ``principal`` is the **total repayable**
(completion keys off ``paid_amount >= principal``), ``total_amount`` is the
goods cash price, and ``monthly_amount * months == principal`` exactly so a
full run of monthly payments closes the contract with no rounding drift.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.money_utils import round_som

VALID_FORMULAS = ("none", "markup", "annuity", "differentiated")

_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class CalcResult:
    total_amount: Decimal   # goods cash price (sum of line totals)
    financed: Decimal       # goods price minus down payment (amount financed)
    principal: Decimal      # total repayable over the schedule (== monthly * months)
    monthly_amount: Decimal  # flat per-month payment (whole so'm)
    months: int
    formula: str


def _q(d: Decimal) -> Decimal:
    """Round to whole so'm via the shared installment-domain rounding (MUDD-65 M-01)."""
    return round_som(d)


def compute_schedule(
    *,
    goods_total: Decimal,
    down_payment: Decimal,
    months: int,
    formula: str | None,
    markup_pct: Decimal | None,
    interest_rate: Decimal | None,
) -> CalcResult:
    """Compute the repayable total + flat monthly payment.

    Raises ``ValueError`` if the financed amount is non-positive or months < 1.
    """
    if months < 1:
        raise ValueError("months_must_be_positive")

    goods_total = Decimal(goods_total)
    down_payment = Decimal(down_payment or 0)
    financed = goods_total - down_payment
    if financed <= 0:
        raise ValueError("down_payment_too_high")

    f = (formula or "none").lower()
    if f not in VALID_FORMULAS:
        f = "none"
    markup = Decimal(markup_pct or 0)
    rate = Decimal(interest_rate or 0)

    # Reject negative financing params loudly. A negative rate/markup would otherwise
    # fall through to "none" (the formula branches require ``> 0``) and silently make
    # the contract interest-free — a silent loss of the shop's margin (the C1 bug
    # class). Zero stays valid (an explicitly interest-free schedule). The API schema
    # also rejects these at the edge; this is the calc function's own input guard.
    if rate < 0:
        raise ValueError("interest_rate_must_be_non_negative")
    if markup < 0:
        raise ValueError("markup_must_be_non_negative")

    if f == "markup":
        repayable = financed * (Decimal(1) + markup / _HUNDRED)
    elif f == "annuity" and rate > 0:
        # Guard: ensure the denominator (1 - (1+r)^-n) != 0
        denominator = Decimal(1) - (Decimal(1) + rate) ** (-months)
        if denominator == 0:
            raise ValueError("invalid_interest_rate_for_months")
        # Equal-payment loan: M = P*r / (1 - (1+r)^-n); total = M*n.
        factor = rate / denominator
        repayable = financed * factor * months
    elif f == "differentiated" and rate > 0:
        # Declining balance: total interest = P·r·(n+1)/2 (principal repaid as P/n each month).
        total_interest = financed * rate * (Decimal(months) + Decimal(1)) / Decimal(2)
        repayable = financed + total_interest
    else:
        # "none", or an interest formula with a zero rate.
        repayable = financed

    # Guard: ensure repayable is always positive
    if repayable <= 0:
        raise ValueError("repayable_must_be_positive")

    monthly = _q(repayable / Decimal(months))
    # Keep monthly * months == principal exactly so a full run closes cleanly.
    principal = monthly * Decimal(months)
    return CalcResult(
        total_amount=_q(goods_total),
        financed=_q(financed),
        principal=principal,
        monthly_amount=monthly,
        months=months,
        formula=f,
    )
