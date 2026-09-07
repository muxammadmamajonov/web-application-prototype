"""(#876) Installment.remaining_balance — the single source of truth for "remaining"
money math (``principal - paid_amount``), consolidated from ~13 inline call sites.

As a ``hybrid_property`` it must behave identically in Python (per-instance, null-guarded)
and in SQL (``func.sum(Installment.remaining_balance)``).
"""
from __future__ import annotations

from decimal import Decimal

from app.models import Installment


def _rb(principal: Decimal, paid: Decimal | None) -> Decimal:
    return Installment(principal=principal, paid_amount=paid).remaining_balance


def test_remaining_balance_normal() -> None:
    assert _rb(Decimal("1000"), Decimal("300")) == Decimal("700")


def test_remaining_balance_zero_when_fully_paid() -> None:
    assert _rb(Decimal("1000"), Decimal("1000")) == Decimal("0")


def test_remaining_balance_negative_on_overpayment() -> None:
    # Preserves the prior inline behaviour: the GROSS balance may go negative. Callers
    # that display it can clamp — centralising makes that a one-line change, not eleven.
    assert _rb(Decimal("1000"), Decimal("1200")) == Decimal("-200")


def test_remaining_balance_null_guards_unflushed_instance() -> None:
    # On an un-flushed instance the columns can be None even though they are NOT NULL.
    assert Installment(principal=Decimal("500")).remaining_balance == Decimal("500")
    assert Installment().remaining_balance == Decimal("0")


def test_remaining_balance_parity_with_old_inline_formula() -> None:
    # Every consolidated site computed exactly ``(principal or 0) - (paid or 0)``.
    cases = [
        (Decimal("0"), Decimal("0")),
        (Decimal("1234567"), Decimal("0")),
        (Decimal("1234567"), Decimal("1000000")),
        (Decimal("999"), Decimal("999")),
        (Decimal("100"), Decimal("250")),
    ]
    for principal, paid in cases:
        old = (principal or Decimal("0")) - (paid or Decimal("0"))
        assert _rb(principal, paid) == old


def test_remaining_balance_sql_expression_mirrors_python() -> None:
    # The hybrid's SQL side compiles to ``principal - paid_amount`` (both columns are
    # NOT NULL, so no COALESCE) — so a SQL aggregate and the Python property agree.
    from sqlalchemy import select

    compiled = str(select(Installment.remaining_balance))
    assert "installments.principal - installments.paid_amount" in compiled, compiled
