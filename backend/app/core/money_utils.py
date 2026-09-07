"""Centralized money rounding logic to ensure consistency across all calculations.

All division and multiplication of money amounts must use ROUND_HALF_UP
to avoid financial loss from accumulated rounding errors.

Example:
    ✓ Correct: 10 / 3 * 3 = 10.00 (using round_amount)
    ✗ Wrong: 10 / 3 * 3 = 9.99 (without proper rounding)

Splitting a total into instalments deliberately does NOT live here — see
``app/services/calc.py:compute_schedule``. That is the domain's single answer to
"how does a total become N payments", and it answers differently on purpose: it
adjusts the *principal* so ``monthly_amount * months == principal`` exactly (every
instalment equal, no odd final payment) and quantises to whole so'm, because UZS
has no minor unit in everyday instalment use. A generic 2-decimal splitter that
dumps the remainder into the last part would contradict both. (LOW-7)
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

__all__ = ["round_amount", "round_som"]


def round_amount(value: Decimal | int | float | str) -> Decimal:
    """Round a money amount to 2 decimal places using ROUND_HALF_UP.

    (LOW-6) ROUND_HALF_UP is *not* banker's rounding — banker's rounding is
    ``ROUND_HALF_EVEN``, which breaks ties toward the even digit. This breaks
    every tie upward, which is the convention the rest of the money model
    (``services/calc._q``) uses; ``tests/test_money_rounding_consistency.py``
    pins the difference.

    Args:
        value: The amount to round (typically a division result).

    Returns:
        Decimal rounded to 2 decimal places using ROUND_HALF_UP.

    Example:
        >>> round_amount("12.345")
        Decimal('12.35')
        >>> round_amount(Decimal("10") / Decimal("3"))
        Decimal('3.33')
    """
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"Cannot convert {value!r} to Decimal") from e

    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_som(value: Decimal | int | float | str) -> Decimal:
    """Round a money amount to whole so'm using ROUND_HALF_UP.

    (MUDD-65 M-01) The instalment/contract domain — ``services.calc``,
    ``services.installments`` (reschedule), and late-fee accrual — settles in
    whole so'm; UZS carries no minor unit in everyday instalment use, and every
    contract's ``principal``/``monthly_amount`` is already a whole number.
    ``round_amount`` above rounds to 2 decimal places for the separate
    billing/subscription domain (tenant balances, plan prices), which does use
    fractional amounts. A late fee computed with ``round_amount`` and then added
    to a whole-so'm ``principal`` produced a fractional-so'm residual that could
    never be paid off — this function is the single place contract-domain code
    must round through instead.

    This is the whole-so'm *quantiser*, not a splitter: turning a total into N
    instalments still belongs to ``services.calc.compute_schedule`` alone (LOW-7).

    Example:
        >>> round_som("12.50")
        Decimal('13')
        >>> round_som(Decimal("1000") / Decimal("3"))
        Decimal('333')
    """
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"Cannot convert {value!r} to Decimal") from e

    return d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
