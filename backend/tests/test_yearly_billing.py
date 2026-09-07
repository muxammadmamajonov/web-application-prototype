"""Yearly billing: one configured discount, derived prices, validated on write.

The yearly price is DERIVED from the monthly one rather than stored beside it.
That is the whole design: two stored prices are two things to keep in step, and
the first reprice that forgets the yearly twin makes the landing page advertise
a discount that does not exist.
"""
from __future__ import annotations

import pytest

from app.services.yearly_billing import (
    DEFAULT_DISCOUNT_PCT,
    MAX_DISCOUNT_PCT,
    YearlyBilling,
    parse,
)


def test_twenty_percent_off_twelve_months() -> None:
    y = YearlyBilling(enabled=True, discount_pct=20)
    assert y.price_for(349_000) == 3_350_400      # 4,188,000 - 20%
    assert y.saving_for(349_000) == 837_600
    assert y.monthly_equivalent(349_000) == 279_200


def test_disabled_means_twelve_months_at_full_price() -> None:
    y = YearlyBilling(enabled=False, discount_pct=20)
    assert y.price_for(349_000) == 349_000 * 12
    assert y.saving_for(349_000) == 0


def test_zero_discount_is_not_a_discount() -> None:
    assert YearlyBilling(True, 0).price_for(149_000) == 149_000 * 12


def test_prices_are_whole_sum() -> None:
    """A fractional so'm is not a price anyone can pay or print."""
    for monthly in (149_000, 349_000, 749_000, 1_490_000, 99_999):
        for pct in (5, 15, 20, 33, 50):
            p = YearlyBilling(True, pct).price_for(monthly)
            assert isinstance(p, int) and p == int(p)


@pytest.mark.parametrize("stored,expected", [
    (None, DEFAULT_DISCOUNT_PCT),
    ({}, DEFAULT_DISCOUNT_PCT),
    ({"discount_pct": "25"}, 25),
    ({"discount_pct": "abc"}, DEFAULT_DISCOUNT_PCT),
    ({"discount_pct": -5}, 0),
    ({"discount_pct": 999}, MAX_DISCOUNT_PCT),
])
def test_a_broken_row_never_produces_a_free_plan(stored, expected) -> None:
    """Read-side is defensive: whatever is in the table, the derived price stays
    a price. Clamping here is the backstop; the write path refuses outright."""
    assert parse(stored).discount_pct == expected


def test_a_hundred_percent_is_not_reachable_through_the_reader() -> None:
    assert parse({"discount_pct": 100}).discount_pct == MAX_DISCOUNT_PCT
    assert YearlyBilling(True, parse({"discount_pct": 100}).discount_pct).price_for(149_000) > 0
