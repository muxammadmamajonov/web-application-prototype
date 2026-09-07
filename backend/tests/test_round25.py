"""Round 25 — SMS fallback (Eskiz.uz) smoke tests.

DB-free checks for the critical wiring:
  * Phone normalisation accepts UZ formats and rejects junk.
  * Part estimation switches between GSM-7 and UCS-2 capacities.
  * HTML stripping turns Telegram templates into plain SMS text.
  * The SMS endpoints are registered under /tenant.
  * NotificationChannel / SmsMessage ORM columns exist.
  * The Alembic migration is chained onto the Round 24 head.
"""
from __future__ import annotations

from app.api.router import api_router
from app.models import NotificationChannel, SmsMessage, TenantSettings
from app.services import sms_eskiz


def test_normalize_uz_phone() -> None:
    assert sms_eskiz.normalize_uz_phone("+998 90 123 45 67") == "998901234567"
    assert sms_eskiz.normalize_uz_phone("998901234567") == "998901234567"
    assert sms_eskiz.normalize_uz_phone("901234567") == "998901234567"
    assert sms_eskiz.normalize_uz_phone("12345") is None
    assert sms_eskiz.normalize_uz_phone(None) is None
    assert sms_eskiz.normalize_uz_phone("1234567890123") is None


def test_estimate_parts_gsm_vs_unicode() -> None:
    # GSM-7 basic set: 160 chars in one part, 161 spills to two.
    assert sms_eskiz.estimate_parts("A" * 160) == 1
    assert sms_eskiz.estimate_parts("A" * 161) == 2
    # Cyrillic forces UCS-2 → 70-char cap.
    assert sms_eskiz.estimate_parts("Ё" * 70) == 1
    assert sms_eskiz.estimate_parts("Ё" * 71) == 2
    assert sms_eskiz.estimate_parts("") == 1


def test_strip_html() -> None:
    assert sms_eskiz.strip_html("<b>Salom</b> 850 000 so'm") == "Salom 850 000 so'm"
    assert sms_eskiz.strip_html("<sub>x</sub>plain") == "xplain"


def test_estimate_cost_scales_with_parts() -> None:
    one = sms_eskiz.estimate_cost_uzs("A" * 10)
    two = sms_eskiz.estimate_cost_uzs("A" * 200)
    assert two == 2 * one


def test_sms_endpoints_registered() -> None:
    paths = {r.path for r in api_router.routes}  # type: ignore[attr-defined]
    assert "/tenant/sms/usage" in paths
    assert "/tenant/sms/history" in paths
    assert "/tenant/customers/{cid}/channels" in paths


def test_orm_columns_exist() -> None:
    nc = set(NotificationChannel.__table__.columns.keys())
    assert {"tenant_id", "customer_id", "channel", "is_primary", "opted_in_at"} <= nc
    sm = set(SmsMessage.__table__.columns.keys())
    assert {"tenant_id", "phone", "text", "parts", "cost_uzs", "status", "kind"} <= sm
    ts = set(TenantSettings.__table__.columns.keys())
    assert {"sms_budget_uzs_per_month", "sms_sender_alpha"} <= ts


def test_migration_chained_onto_round24() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "r25_sms.py"
    spec = importlib.util.spec_from_file_location("r25mig", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "r25sms"
    assert mod.down_revision == "r24commission"
