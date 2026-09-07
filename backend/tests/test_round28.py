"""Round 28 — Inventory ledger + import smoke tests.

DB-free checks for the critical wiring:
  * StockMovement ORM columns + StockMovementKind enum + Product.min_stock_level.
  * record_movement / low_stock_products are wired and callable.
  * _parse_upload handles CSV; _apply_mapping rekeys + drops empties.
  * Inventory + import endpoints are registered.
  * The Alembic migration is chained onto the Round 27 head.
"""
from __future__ import annotations

from app.api.router import api_router
from app.models import Product, StockMovement
from app.models.enums import StockMovementKind


def test_stock_movement_columns_exist() -> None:
    cols = set(StockMovement.__table__.columns.keys())
    assert {
        "tenant_id", "product_id", "qty", "kind", "reason",
        "by_user_id", "related_installment_id",
    } <= cols


def test_stock_movement_kind_enum() -> None:
    assert {k.value for k in StockMovementKind} == {
        "in", "out", "sale", "return", "writeoff", "inventory",
    }


def test_product_has_min_stock_level() -> None:
    assert "min_stock_level" in Product.__table__.columns


def test_inventory_callables() -> None:
    from app.services import inventory

    assert callable(inventory.record_movement)
    assert callable(inventory.low_stock_products)


def test_parse_upload_csv() -> None:
    from app.api.tenant import _parse_upload

    raw = b"name_uz,base_price,stock\niPhone 15,12000000,3\nGalaxy S24,9000000,0\n"
    headers, rows = _parse_upload("products.csv", raw)
    assert headers == ["name_uz", "base_price", "stock"]
    assert len(rows) == 2
    assert rows[0]["name_uz"] == "iPhone 15"
    assert rows[0]["stock"] == "3"


def test_apply_mapping_rekeys_and_drops_empty() -> None:
    from app.api.tenant import _apply_mapping

    rows = [
        {"Mahsulot": "iPhone 15", "Narx": "12000000"},
        {"Mahsulot": "", "Narx": "9000000"},
    ]
    mapping = {"name_uz": "Mahsulot", "base_price": "Narx"}
    out = _apply_mapping(rows, mapping)
    assert out[0] == {"name_uz": "iPhone 15", "base_price": "12000000"}
    # Second row's name_uz is empty -> dropped, only base_price survives.
    assert out[1] == {"base_price": "9000000"}


def test_round28_endpoints_registered() -> None:
    paths = {r.path for r in api_router.routes}  # type: ignore[attr-defined]
    assert "/tenant/products/{pid}/movements" in paths
    assert "/tenant/products/{pid}/stock-adjust" in paths
    assert "/tenant/inventory/low-stock" in paths
    assert "/tenant/import/preview" in paths
    assert "/tenant/products/import" in paths
    assert "/tenant/customers/import" in paths


def test_migration_chained_onto_round27() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "r28_inventory.py"
    spec = importlib.util.spec_from_file_location("r28mig", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "r28inventory"
    assert mod.down_revision == "r27billing"
