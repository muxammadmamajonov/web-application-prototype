"""No aggregate may add two currencies together (MUDD-114).

A shop's `settings.currency` decides only what the NEXT contract is written in;
existing contracts keep the currency they were created with. So a tenant holding
both UZS and USD contracts is an ordinary state — and every money aggregate in
the product used to `func.sum(...)` straight across it, then label the result
with whichever single currency the client happened to pick.

Two tiers here:

* the folding primitive, exercised directly;
* a SOURCE-LEVEL guard over the aggregating modules, because the defect is not
  any one wrong number — it is a `func.sum` over a money column that forgot to
  group. A value test can only catch the sites someone remembered to write a
  test for; this catches the next one added.
"""
from __future__ import annotations

import ast
import inspect
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.currency import (
    DEFAULT_CURRENCY,
    ints_by_currency,
    money_by_currency,
    primary_total,
)

# ── the primitive ──────────────────────────────────────────────────────────

def test_folds_each_currency_separately() -> None:
    got = money_by_currency([("UZS", 100), ("USD", 5), ("UZS", 50)])
    assert got == {"UZS": Decimal(150), "USD": Decimal(5)}


def test_keeps_decimals_because_usd_has_cents() -> None:
    """An int fold would silently drop $0.75 from every USD total."""
    got = money_by_currency([("USD", Decimal("10.75")), ("USD", Decimal("0.25"))])
    assert got == {"USD": Decimal("11.00")}


def test_a_null_currency_is_the_default_not_a_dropped_row() -> None:
    """Rows predating the currency column were backfilled to UZS; discarding
    them would understate a real debt rather than merely mislabel it."""
    assert money_by_currency([(None, 42)]) == {DEFAULT_CURRENCY: Decimal(42)}


def test_an_unsupported_code_is_not_silently_its_own_bucket() -> None:
    """A stray code must not create a currency the product cannot hold — that
    would put money in a bucket no screen knows how to render."""
    assert money_by_currency([("EUR", 7)]) == {DEFAULT_CURRENCY: Decimal(7)}


def test_zero_buckets_are_dropped_so_one_currency_stays_one_currency() -> None:
    """Otherwise a single-currency shop's response would look mixed, and every
    UI written to say "and also…" would say it to everybody."""
    assert money_by_currency([("UZS", 10), ("USD", 0)]) == {"UZS": Decimal(10)}


def test_primary_total_is_zero_rather_than_a_foreign_figure() -> None:
    """The failure this prevents: falling back to *some* number when the asked-for
    currency has none, which is how a USD-labelled tile would show a UZS total."""
    totals = money_by_currency([("USD", 5)])
    assert primary_total(totals, "UZS") == Decimal(0)
    assert primary_total(totals, "USD") == Decimal(5)


def test_ints_by_currency_narrows_only_at_the_edge() -> None:
    assert ints_by_currency({"UZS": Decimal("150.9")}) == {"UZS": 150}


# ── the source-level guard ─────────────────────────────────────────────────

#: Modules whose whole job is aggregating tenant/platform money. `me.py`,
#: `tenant/payments.py` and `platform/tenants.py` were added by the MUDD-122
#: re-audit: the original MUDD-114 scope covered only the aggregates someone
#: remembered were "reports", leaving the widest cross-shop aggregate
#: (`me.py`), the cashier reconciliation figure (`tenant/payments.py`) and the
#: dispute record (`platform/tenants.py`) unguarded.
_AGGREGATORS = (
    "app/services/reports.py",
    "app/api/tenant/customers.py",
    "app/api/platform/overview.py",
    "app/api/me.py",
    "app/api/tenant/payments.py",
    "app/api/platform/tenants.py",
)

#: Columns that are an AMOUNT OF MONEY. Summing any of these without grouping by
#: a currency column is the defect. Counts (`Installment.id`) and quantities
#: (`InstallmentItem.qty`) are deliberately absent — they are unit-free.
_MONEY_COLUMNS = {
    "amount", "principal", "paid_amount", "remaining_balance",
    "monthly_amount", "total_amount", "line_total",
}

#: Named, narrow exceptions — never a whole-module or whole-file exclusion.
#: `TenantTopup` (app/models/tenant.py) has no `currency` column at all: it is
#: the tenant's platform-balance top-up, always UZS by model design (see the
#: `# so'm` comment on `TenantTopup.amount`). Every OTHER sum in these modules
#: must stay grouped by currency.
_ALLOWED_UNGROUPED = {
    "TenantTopup.amount",
}


def _repo_root() -> Path:
    import app

    return Path(inspect.getfile(app)).parent.parent


def _group_by_columns(node: ast.AST) -> set[str]:
    """Attribute names passed to any `.group_by(...)` call inside this statement.

    Structural, not textual: a bare `"currency"` substring check would also
    match a `WHERE Payment.currency == ...` clause, or a `.group_by(Payment.
    status)` call that happens to sit near a `currency` mentioned elsewhere in
    the same statement — both are real false negatives, not hypothetical (see
    the two `test_group_by_*` regression tests below).
    """
    cols: set[str] = set()
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "group_by"
        ):
            for arg in sub.args:
                if isinstance(arg, ast.Attribute):
                    cols.add(arg.attr)
    return cols


def _sum_calls(node: ast.AST) -> list[ast.Call]:
    """Every `func.sum(...)` Call node inside this statement (not `func.count`,
    not the `func.coalesce(...)` that usually wraps it)."""
    return [
        sub for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "sum"
    ]


def _is_currency_guarded(call: ast.Call) -> bool:
    """True if THIS SPECIFIC `func.sum(...)` call's own argument filters on a
    `<Model>.currency ==` comparison — the
    `func.sum(case((Installment.currency == primary, Installment.total_amount),
    else_=0))` pattern used by `reports.ltv`'s ranking sum. Currency-safe
    without a GROUP BY: only rows matching that one currency ever contribute,
    so nothing is added across currencies.
    """
    for sub in ast.walk(call):
        if isinstance(sub, ast.Compare) and any(isinstance(op, ast.Eq) for op in sub.ops):
            for side in (sub.left, *sub.comparators):
                if isinstance(side, ast.Attribute) and side.attr.lower() == "currency":
                    return True
    return False


def _ungrouped_money_sums(source: str, label: str) -> list[str]:
    """Every `func.sum(<money column>)` call in `source` that is neither inside
    a query grouped by a currency column nor itself filtered to one currency,
    `label`-prefixed for the assertion message. Shared by the real-file sweep
    and the synthetic regression tests so both exercise the exact same logic.

    Scoped per `func.sum(...)` CALL, not per statement: a statement can mix a
    currency-guarded sum (safe) with a plain one (unsafe) side by side, and a
    statement-wide pass would let the unsafe one hide behind the safe one.
    """
    tree = ast.parse(source)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr | ast.Assign | ast.Return | ast.AnnAssign):
            continue
        segment = ast.unparse(node)
        if "func.sum(" not in segment or "select(" not in segment:
            # A bare column expression (`revenue = func.sum(...)`) assigned for
            # reuse: its grouping is decided by the query that consumes it, and
            # that query is a separate statement which this check also visits.
            continue
        has_currency_group = any(c.lower() == "currency" for c in _group_by_columns(node))
        for call in _sum_calls(node):
            call_src = ast.unparse(call)
            summed = {
                attr
                for attr in _MONEY_COLUMNS
                if f".{attr})" in call_src or f".{attr}," in call_src or f".{attr} " in call_src
            }
            if not summed:
                continue
            if has_currency_group or _is_currency_guarded(call):
                continue
            if any(allowed in call_src for allowed in _ALLOWED_UNGROUPED):
                continue
            line = getattr(call, "lineno", getattr(node, "lineno", "?"))
            offenders.append(f"{label}:{line} sums {sorted(summed)} with no currency in scope")
    return offenders


@pytest.mark.parametrize("rel", _AGGREGATORS)
def test_every_money_sum_is_grouped_by_currency(rel: str) -> None:
    """Each `func.sum(<money column>)` must sit in a query that also groups by a
    currency column.

    Read off the AST rather than by eye: the point is to fail when SOMEONE ELSE
    adds the next aggregate, which no hand-written value test can anticipate.
    Scoped per statement, so one grouped query in the file cannot vouch for an
    ungrouped one next to it.
    """
    source = (_repo_root() / rel).read_text()
    offenders = _ungrouped_money_sums(source, rel)

    assert not offenders, (
        "a money SUM is not grouped by currency (MUDD-114) — adding UZS to USD:\n  "
        + "\n  ".join(offenders)
    )


def test_group_by_a_different_column_is_not_a_currency_group() -> None:
    """Reproduces a false negative the substring check let through: `currency`
    appears in the statement (as a selected column) but `group_by` only groups
    by `status` — so every status bucket still adds UZS to USD underneath it.
    """
    source = (
        "select(Payment.currency, func.sum(Payment.amount)).group_by(Payment.status)"
    )
    offenders = _ungrouped_money_sums(source, "synthetic")
    assert offenders, "an ungrouped sum with `currency` only in group_by's sibling column must be flagged"


def test_currency_only_in_where_is_not_a_currency_group() -> None:
    """Reproduces the other false negative: `currency` appears, but only in a
    `WHERE` filter — there is no `group_by` at all, so a single-currency
    filter still lets every OTHER currency's rows sum together.
    """
    source = (
        "select(func.sum(Payment.amount)).where(Payment.currency == 'USD')"
    )
    offenders = _ungrouped_money_sums(source, "synthetic")
    assert offenders, "an ungrouped sum with `currency` only in WHERE must be flagged"


def test_group_by_currency_itself_is_not_flagged() -> None:
    """The positive control: a sum actually grouped by currency must pass,
    proving the two regression tests above fail for the right reason and not
    because the helper flags everything."""
    source = (
        "select(Payment.currency, func.sum(Payment.amount)).group_by(Payment.currency)"
    )
    assert not _ungrouped_money_sums(source, "synthetic")


def test_tenant_topup_amount_is_the_one_named_exception() -> None:
    """The exception is scoped to the exact expression, not the file: a SECOND
    ungrouped sum next to it (e.g. a future `Payment.amount` sum with no
    currency grouping) must still be caught."""
    source = (
        "select(func.sum(TenantTopup.amount)).where(TenantTopup.status == 'paid')\n"
        "select(func.sum(Payment.amount)).where(Payment.tenant_id == tid)"
    )
    offenders = _ungrouped_money_sums(source, "synthetic")
    assert len(offenders) == 1 and ":2 " in offenders[0]
