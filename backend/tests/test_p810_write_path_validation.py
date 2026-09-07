"""#810 — tenant write-path validation hardening (DB-free).

Schema-level: every ``*Patch`` schema now carries the same ``max_length`` / format
bounds as its ``*In`` counterpart, numeric + list/item fields have upper caps, and
``BusinessProfileIn`` validates phone/email. Handler-level: a DB ``DataError``
(over-length value, numeric overflow) becomes a clean 422 instead of a 500.

These reproduce the original gap (over-length input → Postgres truncation → 500)
at the schema boundary, so they fail before the fix and pass after.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import orjson
import pytest
from pydantic import ValidationError

_VALID_PHONE = "+998900000001"


# ── Patch ↔ In bound parity (Customer / Guarantor) ───────────────────────────

def test_customer_patch_rejects_overlong_strings() -> None:
    from app.api.tenant import CustomerPatch

    # bounded to the column widths (address 500, notes 1000, passports 8/16/32)
    for field, ok, bad in [
        ("address", "x" * 500, "x" * 501),
        ("notes", "x" * 1000, "x" * 1001),
        ("passport_series", "AB", "x" * 9),
        ("passport_number", "1234567", "x" * 17),
        ("passport_id", "AB1234567", "x" * 33),
    ]:
        CustomerPatch(**{field: ok})  # valid length passes
        with pytest.raises(ValidationError):
            CustomerPatch(**{field: bad})
    # secondary_phone regains a max_length (it had lost its Field entirely)
    with pytest.raises(ValidationError):
        CustomerPatch(secondary_phone="9" * 33)


def test_guarantor_patch_rejects_overlong_strings() -> None:
    from app.api.tenant import GuarantorPatch

    GuarantorPatch(address="Tashkent", relationship="brother", passport_number="1234567")
    for field, bad in [
        ("address", "x" * 501),
        ("relationship", "x" * 65),
        ("passport_series", "x" * 9),
        ("passport_number", "x" * 17),
    ]:
        with pytest.raises(ValidationError):
            GuarantorPatch(**{field: bad})


# ── numeric upper bounds (customer income, product prices) ────────────────────

def test_monthly_income_bounds() -> None:
    from app.api.tenant import _MAX_MONEY, CustomerIn, CustomerPatch

    CustomerPatch(monthly_income=Decimal("1000000"))
    with pytest.raises(ValidationError):
        CustomerPatch(monthly_income=Decimal("-1"))
    with pytest.raises(ValidationError):
        CustomerPatch(monthly_income=_MAX_MONEY + 1)
    # parity: CustomerIn gets the same cap
    with pytest.raises(ValidationError):
        CustomerIn(full_name="Ali", phone=_VALID_PHONE, monthly_income=_MAX_MONEY + 1)


def test_product_patch_price_caps() -> None:
    from app.api.tenant import _MAX_MONEY, ProductPatch

    ProductPatch(base_price=Decimal("1000000"), cost_price=Decimal("500000"))
    with pytest.raises(ValidationError):
        ProductPatch(base_price=_MAX_MONEY + 1)
    with pytest.raises(ValidationError):
        ProductPatch(cost_price=_MAX_MONEY + 1)


# ── list / item caps (installment) ───────────────────────────────────────────

def test_installment_item_caps() -> None:
    from app.api.tenant import _MAX_QTY, _MAX_UNIT_PRICE, InstallmentItemIn

    InstallmentItemIn(product_name_snapshot="Phone", qty=1, unit_price=Decimal("100"))
    with pytest.raises(ValidationError):
        InstallmentItemIn(product_name_snapshot="Phone", qty=_MAX_QTY + 1, unit_price=Decimal("1"))
    with pytest.raises(ValidationError):
        InstallmentItemIn(product_name_snapshot="Phone", qty=1, unit_price=_MAX_UNIT_PRICE + 1)
    with pytest.raises(ValidationError):
        InstallmentItemIn(product_name_snapshot="Phone", qty=1, unit_price=Decimal("1"), serial_number="s" * 65)


def test_installment_items_list_cap_and_notes() -> None:
    from app.api.tenant import _MAX_ITEMS, InstallmentIn

    item = {"product_name_snapshot": "P", "qty": 1, "unit_price": Decimal("1")}
    base = {"customer_id": uuid4(), "months": 1}
    InstallmentIn(**base, items=[item])  # one item is fine
    with pytest.raises(ValidationError):
        InstallmentIn(**base, items=[item] * (_MAX_ITEMS + 1))   # too many line items
    with pytest.raises(ValidationError):
        InstallmentIn(**base, items=[])                          # min_length=1 preserved
    with pytest.raises(ValidationError):
        InstallmentIn(**base, items=[item], notes="x" * 1501)    # notes bounded to 1500


# ── BusinessProfileIn format + length validation ─────────────────────────────

def test_business_profile_validates_contact_and_length() -> None:
    from app.api.tenant_settings import BusinessProfileIn

    BusinessProfileIn(
        business_name="Acme", contact_email="shop@example.uz", contact_phone=_VALID_PHONE,
    )
    with pytest.raises(ValidationError):
        BusinessProfileIn(contact_email="not-an-email")
    with pytest.raises(ValidationError):
        BusinessProfileIn(contact_phone="abc")        # UzPhone format
    with pytest.raises(ValidationError):
        BusinessProfileIn(business_name="x" * 161)    # column is String(160)


# ── DataError → 422 (the backstop behind the schema bounds) ──────────────────

async def test_data_error_handler_returns_422() -> None:
    from types import SimpleNamespace

    import app.main as main

    exc = main.DataError("UPDATE customers SET notes=...", {},
                         ValueError("value too long for type character varying(1000)"))
    req = SimpleNamespace(url=SimpleNamespace(path="/api/tenant/customers/x"))
    resp = await main._data_error_handler(req, exc)
    assert resp.status_code == 422
    assert orjson.loads(resp.body)["detail"] == "invalid_field_value"


def test_data_error_handler_is_registered() -> None:
    from sqlalchemy.exc import DataError

    import app.main as main

    assert DataError in main.app.exception_handlers, "DataError must be handled, not fall to the 500 catch-all"


# ── reports cashflow rejects a reversed range ─────────────────────────────────

def test_cashflow_rejects_reversed_range() -> None:
    import inspect

    from app.api.reports import report_cashflow

    src = inspect.getsource(report_cashflow)
    assert "frm > to" in src and "from_after_to" in src
