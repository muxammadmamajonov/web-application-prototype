"""Round 22 — Telefon-shop UX kit smoke tests.

Pure, DB-free checks for the critical wiring:
  * Customer / Guarantor / Installment Pydantic schemas accept the new fields
    and still enforce JSHSHIR (14-digit) validation.
  * The new ORM models expose the expected columns.
  * The Alembic migration is chained onto the previous head.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.api.tenant import CustomerIn, GuarantorIn, InstallmentIn, InstallmentItemIn
from app.models import Customer, Guarantor, InstallmentGuarantor


def test_customer_in_accepts_employment_fields() -> None:
    c = CustomerIn(
        full_name="Ali Valiyev",
        phone="+998901234567",
        employment_place="Tech LLC",
        monthly_income=Decimal("5000000"),
        mehnat_daftarchasi_photo_urls=["https://x/y.jpg"],
    )
    assert c.employment_place == "Tech LLC"
    assert c.monthly_income == Decimal("5000000")
    assert c.mehnat_daftarchasi_photo_urls == ["https://x/y.jpg"]


def test_guarantor_in_validates_jshshir() -> None:
    g = GuarantorIn(full_name="Olim Aka", phone="+998901112233", jshshir="12345678901234")
    assert g.jshshir == "12345678901234"
    with pytest.raises(ValidationError):
        GuarantorIn(full_name="Bad", phone="+99890", jshshir="123")  # too short


def test_installment_in_accepts_guarantor_ids() -> None:
    body = InstallmentIn(
        customer_id="11111111-1111-1111-1111-111111111111",
        months=6,
        items=[InstallmentItemIn(product_name_snapshot="iPhone 15", unit_price=Decimal("1"))],
        guarantor_ids=["22222222-2222-2222-2222-222222222222"],
    )
    assert len(body.guarantor_ids) == 1
    # Default is an empty list, never None.
    body2 = InstallmentIn(
        customer_id="11111111-1111-1111-1111-111111111111",
        months=3,
        items=[InstallmentItemIn(product_name_snapshot="x", unit_price=Decimal("1"))],
    )
    assert body2.guarantor_ids == []


def test_models_have_round22_columns() -> None:
    cust_cols = set(Customer.__table__.columns.keys())
    assert {"employment_place", "monthly_income", "mehnat_daftarchasi_photo_urls"} <= cust_cols

    g_cols = set(Guarantor.__table__.columns.keys())
    # `relationship` is the DB column name even though the attr is relationship_label.
    assert {"full_name", "phone", "jshshir", "relationship", "photo_urls"} <= g_cols

    link_cols = set(InstallmentGuarantor.__table__.columns.keys())
    assert {"installment_id", "guarantor_id", "sequence"} <= link_cols


def test_migration_chained_onto_previous_head() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "r22_telefon_shop_kit.py"
    spec = importlib.util.spec_from_file_location("r22mig", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "r22telefon"
    assert mod.down_revision == "g7h8payoff"
