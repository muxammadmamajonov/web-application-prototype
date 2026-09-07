"""Test suite for centralized money rounding logic (#747 fix).

Verifies that Decimal.ROUND_HALF_UP is used consistently across all
money calculations, preventing financial loss from rounding errors.
"""
from decimal import Decimal

import pytest

from app.core.money_utils import round_amount


class TestRoundAmount:
    """Test basic rounding with ROUND_HALF_UP."""

    def test_round_amount_basic(self) -> None:
        """Basic rounding of money amounts."""
        assert round_amount("12.345") == Decimal("12.35")
        assert round_amount("12.344") == Decimal("12.34")

    def test_round_amount_from_division(self) -> None:
        """Rounding after division (common operation)."""
        # 10 / 3 = 3.333... → 3.33
        result = round_amount(Decimal("10") / Decimal("3"))
        assert result == Decimal("3.33")

    def test_round_amount_half_up(self) -> None:
        """Verify ROUND_HALF_UP behavior (0.5 rounds up)."""
        # 12.345 rounds to 12.35 (5 rounds up)
        assert round_amount("12.345") == Decimal("12.35")
        # 12.335 rounds to 12.34 (5 rounds up)
        assert round_amount("12.335") == Decimal("12.34")

    def test_round_amount_zero(self) -> None:
        """Rounding zero."""
        assert round_amount("0") == Decimal("0.00")
        assert round_amount(0) == Decimal("0.00")

    def test_round_amount_large_numbers(self) -> None:
        """Rounding larger amounts."""
        assert round_amount("999999.999") == Decimal("1000000.00")
        assert round_amount("1234567.895") == Decimal("1234567.90")

    def test_round_amount_accepts_multiple_types(self) -> None:
        """Rounding accepts str, int, float, Decimal."""
        assert round_amount("10.5") == Decimal("10.50")
        assert round_amount(10.5) == Decimal("10.50")
        assert round_amount(Decimal("10.5")) == Decimal("10.50")
        assert round_amount(10) == Decimal("10.00")

    def test_round_amount_invalid_input(self) -> None:
        """Invalid inputs raise ValueError."""
        with pytest.raises(ValueError):
            round_amount("not_a_number")
        with pytest.raises(ValueError):
            round_amount(None)


class TestMoneyPathConsistency:
    """The rounding policy holds across a realistic money path."""

    def test_invoice_total_consistency(self) -> None:
        """Simulate an invoice with line-item rounding."""
        line_items = [
            Decimal("123.456"),  # Should round to 123.46
            Decimal("234.567"),  # Should round to 234.57
            Decimal("345.678"),  # Should round to 345.68
        ]
        rounded_items = [round_amount(item) for item in line_items]
        total = sum(rounded_items)
        assert total == Decimal("703.71")

    def test_installment_split_has_one_owner(self) -> None:
        """(LOW-7) Splitting a total into instalments belongs to compute_schedule.

        The generic ``split_amount`` helper this module used to export was never
        called by the application and answered the question DIFFERENTLY from the
        domain: it kept the total fixed and made the last part uneven, at 2
        decimals. ``compute_schedule`` keeps every instalment EQUAL and moves the
        principal instead, in whole so'm. Two answers to one question is the bug;
        this pins the surviving one.
        """
        from app.services.calc import compute_schedule

        result = compute_schedule(
            goods_total=Decimal("1000"), down_payment=Decimal("0"),
            months=12, formula="none", markup_pct=None, interest_rate=None,
        )
        # Every month identical, and a full run closes the contract exactly.
        assert result.monthly_amount * result.months == result.principal
        assert result.monthly_amount == round_amount(result.monthly_amount)
