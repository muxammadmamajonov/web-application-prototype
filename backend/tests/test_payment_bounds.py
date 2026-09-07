"""(#138) Tenant payment/installment amount inputs reject zero/negative at the
schema (→422), instead of slipping through to the DB CHECK as a mislabeled 409
(contract_number_conflict) or an uncaught 500 on the final commit."""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.tenant import InstallmentIn, InstallmentItemIn, PaymentIn
from app.models.enums import PaymentMethod


def _item() -> InstallmentItemIn:
    return InstallmentItemIn(product_name_snapshot="phone", qty=1, unit_price=Decimal("100"))


def test_payment_rejects_zero_and_negative_amount() -> None:
    for bad in (Decimal("0"), Decimal("-5")):
        with pytest.raises(ValidationError):
            PaymentIn(installment_id=uuid4(), amount=bad, method=PaymentMethod.cash)


def test_payment_accepts_a_positive_amount() -> None:
    PaymentIn(installment_id=uuid4(), amount=Decimal("100"), method=PaymentMethod.cash)


def test_item_rejects_nonpositive_qty_and_negative_unit_price() -> None:
    with pytest.raises(ValidationError):
        InstallmentItemIn(product_name_snapshot="x", qty=0, unit_price=Decimal("10"))
    with pytest.raises(ValidationError):
        InstallmentItemIn(product_name_snapshot="x", qty=1, unit_price=Decimal("-1"))


def test_installment_rejects_negative_down_payment() -> None:
    with pytest.raises(ValidationError):
        InstallmentIn(customer_id=uuid4(), months=6, items=[_item()], down_payment=Decimal("-1"))


def test_installment_allows_zero_down_payment() -> None:
    InstallmentIn(customer_id=uuid4(), months=6, items=[_item()], down_payment=Decimal("0"))
