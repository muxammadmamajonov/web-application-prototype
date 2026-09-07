"""Round 26 — Plan editor + self-service signup + trial lifecycle smoke tests.

DB-free checks for the critical wiring:
  * Plan capability flags / badge / price_per_installment ORM columns exist.
  * Tenant.trial_ends_at / suspended_at ORM columns exist.
  * Signup + provision-trial + public-plans endpoints are registered.
  * The trial-enforcement cron job constants are sane.
  * The Alembic migration is chained onto the Round 25 head.
"""
from __future__ import annotations

from app.api.router import api_router
from app.models import Plan, Tenant


def test_plan_capability_columns_exist() -> None:
    cols = set(Plan.__table__.columns.keys())
    assert {
        "allow_pdf_template_upload", "allow_api_keys", "allow_outbound_webhooks",
        "sms_included_per_month", "commission_management", "multi_branch",
        "risk_network_access", "reports_advanced", "badge", "price_per_installment",
    } <= cols


def test_tenant_trial_columns_exist() -> None:
    cols = set(Tenant.__table__.columns.keys())
    assert {"trial_ends_at", "suspended_at"} <= cols


def test_round26_endpoints_registered() -> None:
    paths = {r.path for r in api_router.routes}  # type: ignore[attr-defined]
    assert "/auth/signup" in paths
    assert "/auth/provision-trial" in paths
    assert "/plans/public" in paths


def test_enforce_trials_constants() -> None:
    from app.services import scheduler

    assert scheduler.SUSPEND_GRACE_DAYS == 14
    assert scheduler.TRIAL_WARN_OFFSETS == (3, 1, 0)
    assert callable(scheduler.enforce_trials_job)


def test_migration_chained_onto_round25() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "r26_plans.py"
    spec = importlib.util.spec_from_file_location("r26mig", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "r26plans"
    assert mod.down_revision == "r25sms"
