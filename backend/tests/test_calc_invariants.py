"""(#264) Property/invariant tests for the installment money path.

Where ``test_calc.py`` checks hand-picked exact values and ``test_calc_parity.py``
locks the backend to the frontend mirror via golden vectors, this file asserts
the *invariants* that must hold for ANY input — across a wide parametrized grid
of goods/down/months/formula/markup/rate. These catch rounding drift,
monotonicity violations, and breakage of the ``principal == monthly*months``
convention that completion (``paid_amount >= principal``) relies on.
"""
from __future__ import annotations

import itertools
from decimal import Decimal

import pytest

from app.services.calc import VALID_FORMULAS, compute_schedule

# A grid that exercises every formula across realistic UZS magnitudes, with and
# without a down payment, even/odd months, and a spread of markup % / monthly rates.
_GOODS = [Decimal("1000000"), Decimal("3500000"), Decimal("12000000"), Decimal("99999999")]
_DOWN = [Decimal("0"), Decimal("250000"), Decimal("1000000")]
_MONTHS = [1, 2, 3, 6, 7, 12, 18, 24, 36]
_MARKUP = [Decimal("0"), Decimal("5"), Decimal("12.5"), Decimal("30"), Decimal("100")]
_RATE = [Decimal("0"), Decimal("0.01"), Decimal("0.025"), Decimal("0.03")]


def _valid_grid():
    """Yield only kwargs whose financed amount is positive (the computable case)."""
    for goods, down, months, formula in itertools.product(_GOODS, _DOWN, _MONTHS, VALID_FORMULAS):
        if goods - down <= 0:
            continue
        markup = _MARKUP if formula == "markup" else [Decimal("0")]
        rate = _RATE if formula in ("annuity", "differentiated") else [Decimal("0")]
        for m, r in itertools.product(markup, rate):
            yield dict(goods_total=goods, down_payment=down, months=months,
                       formula=formula, markup_pct=m, interest_rate=r)


_GRID = list(_valid_grid())


def _id(kw: dict) -> str:
    return f"{kw['formula']}-g{kw['goods_total']}-d{kw['down_payment']}-n{kw['months']}-m{kw['markup_pct']}-r{kw['interest_rate']}"


@pytest.mark.parametrize("kw", _GRID, ids=_id)
def test_core_invariants_hold(kw: dict) -> None:
    r = compute_schedule(**kw)
    financed = kw["goods_total"] - kw["down_payment"]

    # The convention a full payment run closes on — must be EXACT, no drift.
    assert r.monthly_amount * r.months == r.principal
    assert r.monthly_amount >= 0
    assert r.months == kw["months"]
    # Cash price + financed are just rounded passthroughs.
    assert r.total_amount == kw["goods_total"].quantize(Decimal("1"))
    assert r.financed == financed.quantize(Decimal("1"))
    # Repayable is never below financed for these formulas; rounding to whole
    # so'm can shave at most (months * 0.5), so bound principal from below.
    assert r.principal >= financed - Decimal(kw["months"])


def test_grid_is_substantial_and_covers_all_formulas() -> None:
    assert len(_GRID) > 200
    assert {kw["formula"] for kw in _GRID} == set(VALID_FORMULAS)


@pytest.mark.parametrize("months", _MONTHS)
def test_zero_cost_formulas_are_equivalent(months: int) -> None:
    """none ≡ annuity@rate0 ≡ differentiated@rate0 ≡ markup@0% — all interest-free."""
    base = dict(goods_total=Decimal("6000000"), down_payment=Decimal("500000"), months=months)
    none = compute_schedule(**base, formula="none", markup_pct=None, interest_rate=None)
    ann0 = compute_schedule(**base, formula="annuity", markup_pct=None, interest_rate=Decimal("0"))
    dif0 = compute_schedule(**base, formula="differentiated", markup_pct=None, interest_rate=Decimal("0"))
    mk0 = compute_schedule(**base, formula="markup", markup_pct=Decimal("0"), interest_rate=None)
    assert none.principal == ann0.principal == dif0.principal == mk0.principal


@pytest.mark.parametrize("months", [2, 6, 12, 24])
def test_financing_cost_is_nonnegative_and_grows_with_rate(months: int) -> None:
    """Higher markup/rate never reduces the repayable principal."""
    base = dict(goods_total=Decimal("10000000"), down_payment=Decimal("0"), months=months)
    # markup monotonic
    prev = compute_schedule(**base, formula="markup", markup_pct=Decimal("0"), interest_rate=None).principal
    for m in (Decimal("5"), Decimal("20"), Decimal("50")):
        cur = compute_schedule(**base, formula="markup", markup_pct=m, interest_rate=None).principal
        assert cur >= prev
        prev = cur
    # annuity & differentiated monotonic in rate
    for formula in ("annuity", "differentiated"):
        prev = compute_schedule(**base, formula=formula, markup_pct=None, interest_rate=Decimal("0")).principal
        for rate in (Decimal("0.01"), Decimal("0.02"), Decimal("0.03")):
            cur = compute_schedule(**base, formula=formula, markup_pct=None, interest_rate=rate).principal
            assert cur >= prev
            prev = cur


def test_down_payment_too_high_raises_across_grid() -> None:
    for goods, down in [(Decimal("5000000"), Decimal("5000000")),  # financed == 0
                        (Decimal("3000000"), Decimal("4000000"))]:  # financed < 0
        for formula in VALID_FORMULAS:
            with pytest.raises(ValueError, match="down_payment_too_high"):
                compute_schedule(goods_total=goods, down_payment=down, months=6,
                                 formula=formula, markup_pct=Decimal("10"), interest_rate=Decimal("0.02"))


@pytest.mark.parametrize("months", [0, -1, -12])
def test_non_positive_months_raises(months: int) -> None:
    with pytest.raises(ValueError, match="months_must_be_positive"):
        compute_schedule(goods_total=Decimal("1000000"), down_payment=Decimal("0"),
                         months=months, formula="none", markup_pct=None, interest_rate=None)
