"""Every mutation of a tenant's balance must leave a ledger row behind.

`tenants.balance` is a MATERIALISED total: the authoritative history is
`tenant_balance_txns`, and the column exists so a screen can show a number
without summing the ledger. That is legitimate denormalisation — but it is only
true while the two are written together, and nothing enforced that.

Six independent sites mutate the balance today:

    api/tenant/billing.py            plan charge
    api/platform/tenants.py          admin adjustment
    api/webhooks/subscription.py     provider-confirmed top-up
    services/subscription_checkout.py  approved top-up
    services/billing.py              invoice settlement
    services/referral.py             referral reward

Each pairs its own write with its own `TenantBalanceTxn(...)`, by convention.
`services/referral.py` even says so out loud — *"mirrors api.platform._apply_balance
— inlined to avoid an api<-service import cycle"*. Convention is not enforcement:
a seventh site, or an edit to one of these six, can drop the ledger row and
nothing fails. What you get is a balance that no history explains, on money, in a
product whose whole subject is who owes what.

The runtime behaviour of each path is covered by its own tests. What none of them
can cover is the path nobody wrote yet — so this walks the AST and requires any
function that assigns `<something>.balance` to also construct a
`TenantBalanceTxn` in the same function.

This is a GUARD, not a refactor: it changes no behaviour and forbids no design.
Consolidating the six into one helper would satisfy it too.
"""
from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

LEDGER = "TenantBalanceTxn"

#: Reading a balance is fine; only assignment materialises a new total.
_BALANCE_ATTR = "balance"


def _assigns_balance(node: ast.AST) -> bool:
    """`x.balance = …` or `x.balance += …` anywhere under `node`."""
    for sub in ast.walk(node):
        targets: list[ast.expr] = []
        if isinstance(sub, ast.Assign):
            targets = list(sub.targets)
        elif isinstance(sub, ast.AugAssign):
            targets = [sub.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and t.attr == _BALANCE_ATTR:
                return True
    return False


def _builds_ledger_row(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == LEDGER:
            return True
    return False


def _functions() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    out: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                out.append((str(path.relative_to(APP.parent)), node))
    return out


def test_the_scan_actually_finds_the_known_writers() -> None:
    """Guard the guard.

    If the AST walk stops matching — a refactor renames the model, the column
    moves — every assertion below passes over an empty set and reads as coverage
    while checking nothing.
    """
    writers = [
        f"{rel}:{fn.name}" for rel, fn in _functions() if _assigns_balance(fn)
    ]
    assert len(writers) >= 5, (
        "expected to find the known balance writers; found "
        f"{len(writers)}: {writers}"
    )


def test_every_balance_writer_also_writes_the_ledger() -> None:
    offenders = [
        f"{rel}:{fn.lineno} {fn.name}()"
        for rel, fn in _functions()
        if _assigns_balance(fn) and not _builds_ledger_row(fn)
    ]
    assert offenders == [], (
        "these change a tenant's balance without recording why:\n  "
        + "\n  ".join(offenders)
        + "\n\n`tenants.balance` is a materialised total over `tenant_balance_txns`. "
          "A mutation with no ledger row leaves a balance no history explains — "
          "on money. Add the TenantBalanceTxn, or route the change through a "
          "helper that does."
    )


def test_the_ledger_row_records_the_resulting_balance() -> None:
    """`balance_after` is what makes the ledger auditable rather than a log.

    Without it a reader can only reconstruct the running total by replaying every
    row in order and trusting that none is missing — which is precisely the
    property in doubt when a balance and its history disagree.
    """
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == LEDGER):
                continue
            if not any(kw.arg == "balance_after" for kw in node.keywords):
                offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
    assert offenders == [], (
        "these ledger rows omit `balance_after`:\n  " + "\n  ".join(offenders)
    )
