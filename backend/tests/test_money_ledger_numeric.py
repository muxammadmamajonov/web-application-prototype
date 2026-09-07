"""#69 — the tenant prepaid-balance ledger uses Numeric(14,2), not Integer, and
its money invariants are enforced by CHECK constraints.

Schema-level guards (the suite has no live DB yet, #79): the four ledger money
columns must be Numeric(14,2) so ledger money is exact and type-consistent with
the rest of the money model (plans, installments, payments), and the
non-negativity / positivity invariants must be enforced in the DB, not just the
app. A final source guard keeps a raw Decimal from leaking into a JSON/audit
payload (orjson/json can't encode Decimal — it would 500 the endpoint).
"""
from __future__ import annotations

import pytest
from sqlalchemy import Numeric


@pytest.mark.parametrize(
    "model_name, col",
    [
        ("Tenant", "balance"),
        ("TenantBalanceTxn", "amount"),
        ("TenantBalanceTxn", "balance_after"),
        ("TenantTopup", "amount"),
    ],
)
def test_ledger_money_columns_are_numeric_14_2(model_name: str, col: str) -> None:
    import app.models as m

    model = getattr(m, model_name)
    coltype = model.__table__.c[col].type
    assert isinstance(coltype, Numeric), f"{model_name}.{col} is {coltype!r}, expected Numeric"
    assert (coltype.precision, coltype.scale) == (14, 2)


def test_ledger_check_constraints_present() -> None:
    from app.models import Tenant, TenantBalanceTxn, TenantTopup

    def checks(model: type) -> set[str]:
        return {
            c.name for c in model.__table__.constraints
            if c.__class__.__name__ == "CheckConstraint"
        }

    assert "ck_tenants_balance_nonneg" in checks(Tenant)
    assert "ck_tenant_balance_txns_balance_after_nonneg" in checks(TenantBalanceTxn)
    assert "ck_tenant_topups_amount_positive" in checks(TenantTopup)


def test_balance_serialization_wraps_decimal_as_int() -> None:
    # A Decimal must never reach a JSON/audit payload. Both the tenant-facing and
    # platform-admin balance/top-up serializers wrap in int(); guard by source so
    # a raw Decimal can't regress back into a response.
    import pkgutil
    from pathlib import Path

    import app.api.platform as platform
    import app.api.tenant as tenant_api

    # The tenant API is a package: read every module in it, so the guard still
    # covers the same handlers it did when they lived in one file.
    tsrc = "".join(
        Path(m.module_finder.path, f"{m.name}.py").read_text(encoding="utf-8")
        for m in pkgutil.iter_modules(tenant_api.__path__)
    )
    assert '"balance": int(' in tsrc
    assert '"amount": int(' in tsrc

    # The platform API is a package too — same reasoning as the tenant read above.
    psrc = "".join(
        Path(m.module_finder.path, f"{m.name}.py").read_text(encoding="utf-8")
        for m in pkgutil.iter_modules(platform.__path__)
    )
    assert '"balance": int(' in psrc
    assert '"amount": int(' in psrc
