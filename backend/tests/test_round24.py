"""Round 24 — Owner reports smoke tests.

DB-free checks for the critical wiring:
  * Export helpers produce CSV / XLSX / PDF bytes from a column+row spec.
  * The 6 report endpoints are registered under /tenant/reports.
  * Commission columns exist on the ORM models.
  * The Alembic migration is chained onto the Round 23 head.
"""
from __future__ import annotations

import pytest

from app.api import reports
from app.api.router import api_router
from app.models import TenantSettings, TenantUser


def test_csv_export_has_header_and_rows() -> None:
    cols = [("a", "Alpha"), ("b", "Beta")]
    rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    out = reports._csv_bytes(cols, rows).decode("utf-8-sig")
    lines = out.strip().splitlines()
    assert lines[0] == "Alpha,Beta"
    assert lines[1] == "1,2"
    assert len(lines) == 3


def test_xlsx_export_produces_bytes() -> None:
    cols = [("k", "Key"), ("v", "Val")]
    rows = [{"k": "revenue", "v": "100"}]
    xlsx = reports._xlsx_bytes("Margin", cols, rows)
    # XLSX is a zip → "PK" magic bytes.
    assert xlsx[:2] == b"PK"


def test_pdf_export_produces_bytes() -> None:
    cols = [("k", "Key"), ("v", "Val")]
    rows = [{"k": "revenue", "v": "100"}]
    try:
        pdf = reports._pdf_bytes("Margin", cols, rows)
    except OSError:
        # WeasyPrint's native libs (pango/cairo) aren't installed in every
        # local dev env — they are in the Docker prod image. Skip rather than
        # fail when the system libraries are absent.
        pytest.skip("WeasyPrint system libs unavailable in this env")
    assert pdf[:4] == b"%PDF"


def test_report_endpoints_registered() -> None:
    paths = {r.path for r in api_router.routes}  # type: ignore[attr-defined]
    for sub in ("aging", "cashflow", "staff-performance", "products", "ltv", "margin"):
        assert f"/tenant/reports/{sub}" in paths


def test_commission_columns_exist() -> None:
    assert "commission_rate_pct" in set(TenantSettings.__table__.columns.keys())
    assert "commission_rate_pct" in set(TenantUser.__table__.columns.keys())


def test_migration_chained_onto_round23() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "r24_commission.py"
    spec = importlib.util.spec_from_file_location("r24mig", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "r24commission"
    assert mod.down_revision == "r23risknet"
