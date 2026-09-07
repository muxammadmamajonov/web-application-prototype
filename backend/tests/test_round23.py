"""Round 23 — Cross-tenant risk network smoke tests.

Pure, DB-free checks for the critical wiring:
  * JSHSHIR hashing is deterministic, salted, and rejects non-14-digit ids.
  * Severity thresholds map days-overdue → the right enum.
  * The tier heuristic escalates correctly on external signals.
  * The risk-report schema defaults to a fraud report.
  * The new ORM model + opt-in column exist.
  * The Alembic migration is chained onto the Round 22 head.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.tenant import RiskReportIn
from app.models import CustomerRiskSignal, TenantSettings
from app.models.enums import RiskSeverity
from app.services import risk


def test_hash_jshshir_deterministic_and_validates() -> None:
    a = risk.hash_jshshir("12345678901234")
    b = risk.hash_jshshir("12345678901234")
    assert a == b and len(a) == 64
    # Different id → different hash.
    assert a != risk.hash_jshshir("12345678901235")
    # Non-14-digit → None (nothing to match cross-tenant).
    assert risk.hash_jshshir("123") is None
    assert risk.hash_jshshir(None) is None
    # Normalisation strips spaces/dashes.
    assert risk.hash_jshshir("1234 5678 9012 34") == a


def test_severity_for_days_overdue() -> None:
    assert risk.severity_for_days_overdue(10) is None
    assert risk.severity_for_days_overdue(30) == RiskSeverity.late_30
    assert risk.severity_for_days_overdue(60) == RiskSeverity.late_60
    assert risk.severity_for_days_overdue(95) == RiskSeverity.late_90


def test_tier_escalates_on_external_signals() -> None:
    low_internal = {"active_overdue": 0, "late_fee_count": 0}
    assert risk._tier(low_internal, {"signal_count": 0, "worst_severity": None}) == "low"
    assert risk._tier(low_internal, {"signal_count": 1, "worst_severity": "late_60"}) == "medium"
    assert risk._tier(low_internal, {"signal_count": 3, "worst_severity": "late_60"}) == "high"
    assert risk._tier(low_internal, {"signal_count": 1, "worst_severity": "fraud_reported"}) == "high"
    # Internal-only overdue → medium even with no external signals.
    assert risk._tier({"active_overdue": 1, "late_fee_count": 0}, {"signal_count": 0}) == "medium"


def test_risk_report_in_defaults_to_fraud() -> None:
    body = RiskReportIn(customer_id="11111111-1111-1111-1111-111111111111")
    assert body.severity == "fraud_reported"
    with pytest.raises(ValidationError):
        RiskReportIn(customer_id="11111111-1111-1111-1111-111111111111", severity="late_30")


def test_models_have_round23_columns() -> None:
    sig_cols = set(CustomerRiskSignal.__table__.columns.keys())
    assert {"jshshir_hash", "severity", "source", "status",
            "appeal_deadline", "source_tenant_id", "evidence_json"} <= sig_cols
    assert "risk_network_opt_in" in set(TenantSettings.__table__.columns.keys())


def test_migration_chained_onto_round22() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "r23_risk_network.py"
    spec = importlib.util.spec_from_file_location("r23mig", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "r23risknet"
    assert mod.down_revision == "r22telefon"
