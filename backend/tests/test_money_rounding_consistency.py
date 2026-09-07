"""#747 — money rounding is consistent (ROUND_HALF_UP everywhere, no banker's).

Python's ``Decimal.quantize()`` defaults to ROUND_HALF_EVEN (banker's rounding)
when no ``rounding=`` is given. That diverges from this project's documented
money policy — ROUND_HALF_UP (``app/core/money_utils.round_amount`` / ``calc._q``)
— and rounds .5 cases DOWN half the time, so the business silently under-collects
(the "money loss" in #747).

This guard fails if any ``app/`` module reintroduces a bare ``.quantize(...)``
without an explicit rounding mode. Build money amounts via ``round_amount()`` (or
pass ``rounding=ROUND_HALF_UP``) instead.
"""
from __future__ import annotations

import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def test_no_money_quantize_relies_on_default_bankers_rounding() -> None:
    """Checked on the AST, not line by line.

    This scan used to read one line at a time and pass any line containing
    ``rounding=``. Two blind spots followed from that, and both matter: a call
    formatted across lines had its ``rounding=`` on a DIFFERENT line and was
    reported as an offender (a false alarm that pressures the next person into
    reformatting to appease the test), and — the dangerous direction — a genuinely
    BARE ``.quantize(`` split across lines was invisible to it entirely. A guard
    that can be evaded by pressing Enter is not a guard.

    The AST sees the call whatever its formatting.
    """
    import ast

    offenders: list[str] = []
    for py in APP.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "quantize"):
                continue
            if any(kw.arg == "rounding" for kw in node.keywords):
                continue
            # `quantize(exp, rounding)` positionally is also explicit.
            if len(node.args) >= 2:
                continue
            offenders.append(f"{py.relative_to(APP)}:{node.lineno}: bare .quantize()")
    assert offenders == [], (
        "bare .quantize() defaults to banker's rounding (ROUND_HALF_EVEN), "
        "violating the ROUND_HALF_UP money policy (#747). Use round_amount() or "
        "pass rounding=ROUND_HALF_UP:\n  " + "\n  ".join(offenders)
    )


def test_round_amount_is_half_up_not_bankers() -> None:
    """The canonical helper rounds .5 UP (half-up), never to-even (banker's)."""
    from decimal import Decimal

    from app.core.money_utils import round_amount

    # Banker's would give 0.12 / 0.14 (round-to-even); half-up gives 0.13 / 0.15.
    assert round_amount(Decimal("0.125")) == Decimal("0.13")
    assert round_amount(Decimal("0.145")) == Decimal("0.15")
