"""(#134) Plan price/limit/overage inputs reject negatives at the schema (→422),
so a negative never reaches compute_amount / suggest_upgrade. Zero stays valid
(a free plan)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.platform import PlanCreateIn, PlanOverridesIn, PlanPatchIn


def test_create_rejects_negative_price() -> None:
    with pytest.raises(ValidationError):
        PlanCreateIn(code="lite", name_uz="a", name_ru="b", price_monthly=-1)


def test_create_allows_zero_price() -> None:  # free/demo plan is valid
    PlanCreateIn(code="lite", name_uz="a", name_ru="b", price_monthly=0)


def test_patch_rejects_negative_price_and_limit() -> None:
    with pytest.raises(ValidationError):
        PlanPatchIn(price_per_installment=-5)
    with pytest.raises(ValidationError):
        PlanPatchIn(max_staff=-1)


def test_overrides_rejects_negative_overage_price() -> None:
    with pytest.raises(ValidationError):
        PlanOverridesIn(overage={"contract": {"price": -10, "enabled": True}})


def test_overrides_rejects_negative_limit() -> None:
    with pytest.raises(ValidationError):
        PlanOverridesIn(limits={"max_active": -3})


def test_overrides_allows_valid_values() -> None:
    PlanOverridesIn(
        price_monthly=5000,
        limits={"max_active": 10},
        overage={"contract": {"price": 100, "enabled": True}},
    )
