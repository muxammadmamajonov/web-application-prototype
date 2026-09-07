"""No production path may create a contract or a payment without a currency.

The runtime end of this is covered by `test_fb021_contract_currency_is_immutable`
— what a contract says after a settings change, what a payment inherits, what
the database refuses. What that cannot cover is a path nobody exercised: a new
webhook, a new admin action, a seeding script. A missing `currency=` there is an
IntegrityError in production rather than a wrong label, but only once somebody
reaches it.

So this walks the AST of every module under `app/` and requires each
`Installment(...)` / `Payment(...)` construction to pass a currency, sourced from
the right place: a contract snapshots the tenant's setting, a payment copies its
parent contract and never re-reads the setting.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"

#: A payment's currency may only come from the contract it lands on. The names
#: differ by call site (`inst`, `ins`, `locked`), the attribute does not.
_FROM_CONTRACT = ".currency"

#: A contract's currency may only come from the resolver, which is the one place
#: allowed to read `tenant_settings.currency`.
_SNAPSHOT = "currency_for_new_contract"


def _constructions(cls: str) -> list[tuple[str, int, ast.Call]]:
    out: list[tuple[str, int, ast.Call]] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == cls):
                out.append((str(path.relative_to(APP.parent)), node.lineno, node))
    return out


def _kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def test_the_walker_actually_finds_the_known_call_sites() -> None:
    """A broken walk would make every assertion below vacuously true."""
    installments = _constructions("Installment")
    payments = _constructions("Payment")
    assert len(installments) >= 1, installments
    assert len(payments) >= 4, payments
    files = {f for f, _, _ in payments}
    # The five real payment writers: tenant-recorded, customer initiate,
    # customer receipt upload, the Payme webhook, and the late-fee cron.
    assert "app/api/tenant/payments.py" in files
    assert "app/api/me.py" in files
    assert "app/api/webhooks/payme.py" in files
    assert "app/services/scheduler.py" in files


def test_every_contract_construction_snapshots_the_tenant_currency() -> None:
    missing: list[str] = []
    wrong: list[str] = []
    for where, line, call in _constructions("Installment"):
        value = _kwarg(call, "currency")
        if value is None:
            missing.append(f"{where}:{line}")
            continue
        if _SNAPSHOT not in ast.unparse(value):
            wrong.append(f"{where}:{line}  currency={ast.unparse(value)}")
    assert missing == [], f"contract created with no currency: {missing}"
    assert wrong == [], (
        f"a contract's currency must come from {_SNAPSHOT}(), the only place "
        f"allowed to read the tenant setting: {wrong}"
    )


def test_every_payment_construction_copies_its_parent_contract() -> None:
    missing: list[str] = []
    wrong: list[str] = []
    for where, line, call in _constructions("Payment"):
        value = _kwarg(call, "currency")
        if value is None:
            missing.append(f"{where}:{line}")
            continue
        text = ast.unparse(value)
        if not text.endswith(_FROM_CONTRACT) or _SNAPSHOT in text:
            wrong.append(f"{where}:{line}  currency={text}")
    assert missing == [], f"payment created with no currency: {missing}"
    assert wrong == [], (
        f"a payment's currency must be copied from its parent contract "
        f"(`<installment>.currency`), never re-read from the shop: {wrong}"
    )


def test_the_payment_construction_reads_the_row_it_writes_against() -> None:
    """`currency=X.currency` and `installment_id=X.id` must name the SAME row.

    Copying from a different installment than the one being credited would pass
    the previous test and still be wrong — and the composite foreign key would
    then reject it at flush, in production, on a money path.
    """
    mismatched: list[str] = []
    for where, line, call in _constructions("Payment"):
        currency = _kwarg(call, "currency")
        parent = _kwarg(call, "installment_id")
        if currency is None or parent is None:
            continue
        cur_src, par_src = ast.unparse(currency), ast.unparse(parent)
        if cur_src.rsplit(".", 1)[0] != par_src.rsplit(".", 1)[0]:
            mismatched.append(f"{where}:{line}  {par_src} vs {cur_src}")
    assert mismatched == [], mismatched


def test_only_the_resolver_reads_the_tenant_currency_setting() -> None:
    """Every other read is how the render-time lookup grew to six call sites.

    Two exemptions, both correct because nothing is being relabelled: the
    settings API echoes the stored value back to the screen that edits it, and
    the resolver itself is where the read belongs.
    """
    allowed = {
        "app/core/currency.py",            # the resolver
        "app/api/tenant_settings.py",      # the editor echoing its own field
        "app/models/settings.py",          # the column definition
        "app/schemas/auth.py",             # the shop-level field on /auth/me
        "app/api/auth.py",                 # …and the query that fills it
    }
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        rel = str(path.relative_to(APP.parent))
        if rel in allowed:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if "TenantSettings.currency" in line or "settings_row.currency" in line:
                offenders.append(f"{rel}:{n}  {line.strip()[:90]}")
            if "settings.currency" in line and "tenant_settings" not in line.lower():
                offenders.append(f"{rel}:{n}  {line.strip()[:90]}")
    assert offenders == [], (
        "a contract- or payment-scoped surface must read the row's own "
        f"currency, not the shop's setting: {offenders}"
    )


def test_the_platform_wallet_currency_is_fixed_and_not_the_shops() -> None:
    """The top-up balance is platform money; a shop's setting cannot move it."""
    from app.core.card_networks import PLATFORM_BALANCE_CURRENCY

    assert PLATFORM_BALANCE_CURRENCY == "UZS"
    source = (APP / "api/tenant/billing.py").read_text()
    assert "PLATFORM_BALANCE_CURRENCY" in source
    # The top-up endpoints must not reach for the tenant's own currency at all.
    assert "TenantSettings" not in source


def test_the_list_filter_and_the_submission_check_share_one_matrix() -> None:
    """Two copies of "which card is compatible" would let the UI and the API
    disagree, and the API is the one holding the money."""
    source = (APP / "api/tenant/billing.py").read_text()
    assert "compatible_cards(" in source          # the list endpoint
    assert "rejection_code(" in source            # the submission endpoint
    # Neither may re-derive eligibility locally.
    for smell in ("COMPATIBLE_NETWORKS[", "== 'UZCARD'", '== "UZCARD"',
                  "startswith(", "number[:"):
        assert smell not in source, smell


@pytest.mark.parametrize("model", ["Installment", "Payment"])
def test_the_column_has_no_python_default(model) -> None:
    """A default would let a forgotten path mint a silently-mislabelled row."""
    from app.models import Installment, Payment

    column = {"Installment": Installment, "Payment": Payment}[model].__table__.c.currency
    assert column.default is None
    assert column.server_default is None
    assert column.nullable is False
