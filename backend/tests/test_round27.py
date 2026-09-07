"""Round 27 — Subscription billing smoke tests.

DB-free checks for the critical wiring:
  * Invoice ORM columns exist + InvoiceStatus enum.
  * Billing math: fixed plans flat, Custom = active × unit_price.
  * Period-bounds helper returns calendar-month edges.
  * suggest_custom_switch / cron jobs are wired and callable.
  * Tenant billing endpoints + invoice PDF route are registered.
  * The Alembic migration is chained onto the Round 26 head.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.api.router import api_router
from app.models import Invoice
from app.models.enums import InvoiceStatus


def test_invoice_columns_exist() -> None:
    cols = set(Invoice.__table__.columns.keys())
    assert {
        "tenant_id", "plan_code", "period_start", "period_end",
        "active_installments_count", "unit_price", "amount", "status",
        "line_items", "issued_at", "due_at", "paid_at", "payment_method",
    } <= cols


def test_invoice_status_enum() -> None:
    assert {s.value for s in InvoiceStatus} == {"open", "paid", "overdue", "void"}


def test_compute_amount_within_included_limit() -> None:
    """R39: subscription + overage — no overage while under the included quota."""
    from app.services import billing

    plan = SimpleNamespace(
        code="business", name_uz="Business",
        price_monthly=Decimal("199000"), max_active_installments=500,
        overage={"contract": {"price": 700, "enabled": True, "mode": "soft"}},
    )
    amount, items = billing.compute_amount(plan, active_count=42)
    assert amount == Decimal("199000.00")
    assert items["model"] == "subscription_overage"


def test_compute_amount_with_contract_overage() -> None:
    """Flex-style: included 0, every active contract billed at the overage rate
    on top of the base fee (49 000 + 3 × 1 500 = 53 500)."""
    from app.services import billing

    plan = SimpleNamespace(
        code="flex", name_uz="Flex",
        price_monthly=Decimal("49000"), max_active_installments=0,
        overage={"contract": {"price": 1500, "enabled": True, "mode": "soft"}},
    )
    amount, items = billing.compute_amount(plan, active_count=3)
    assert amount == Decimal("53500.00")
    assert items["model"] == "subscription_overage"
    assert items["lines"][1]["qty"] == 3


def test_period_bounds_calendar_month() -> None:
    from app.services.billing import _period_bounds

    start, end = _period_bounds(date(2026, 2, 15))
    assert start == date(2026, 2, 1)
    assert end == date(2026, 2, 28)

    start, end = _period_bounds(date(2026, 12, 9))
    assert start == date(2026, 12, 1)
    assert end == date(2026, 12, 31)


def test_billing_callables_and_constants() -> None:
    from app.services import billing, scheduler

    assert callable(billing.run_billing_cycle)
    assert callable(billing.run_dunning)
    assert callable(billing.suggest_custom_switch)
    assert callable(scheduler.run_billing_cycle_job)
    assert callable(scheduler.run_dunning_job)
    assert billing.SUSPEND_AFTER_DUE_DAYS == 3
    assert billing.BLOCK_AFTER_DUE_DAYS == 14


def test_round27_endpoints_registered() -> None:
    paths = {r.path for r in api_router.routes}  # type: ignore[attr-defined]
    assert "/tenant/billing" in paths
    assert "/tenant/invoices" in paths
    assert "/tenant/plan-change" in paths
    assert "/tenant/invoices/{invoice_id}/invoice.pdf" in paths


def test_migration_chained_onto_round26() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "r27_billing.py"
    spec = importlib.util.spec_from_file_location("r27mig", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "r27billing"
    assert mod.down_revision == "r26plans"
