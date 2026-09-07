"""(#136) The shared UZ-phone validator: reject garbage on the auth/claim/demo
paths while accepting all common formats and preserving the caller's value."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from app.core.validators import UzPhone, UzPhoneOpt


class _Required(BaseModel):
    phone: UzPhone


class _Optional(BaseModel):
    phone: UzPhoneOpt = None


def test_every_accepted_spelling_collapses_to_one_stored_form() -> None:
    # (r96) These used to be preserved verbatim. That was the uniqueness hole:
    # `uq_customer_tenant_phone` compared the raw column, so one shop could hold
    # the same person four times over. One canonical form is what makes "is this
    # number already taken?" answerable at all.
    for p in ("+998901234567", "998901234567", "+998 90 123 45 67", "901234567"):
        assert _Required(phone=p).phone == "+998901234567" 


def test_garbage_phones_are_rejected() -> None:
    for bad in ("abc", "12345", "+1 555 0100", "99890"):
        with pytest.raises(ValidationError):
            _Required(phone=bad)


def test_optional_allows_empty_or_missing() -> None:
    assert _Optional().phone is None
    assert _Optional(phone=None).phone is None
    assert _Optional(phone="").phone == ""


# (#262) The validator is now wired into the tenant-dashboard Customer/Guarantor
# schemas too — previously these were length-checked only and accepted garbage.
from app.api.tenant import CustomerIn, CustomerPatch, GuarantorIn, GuarantorPatch


def test_customer_in_rejects_garbage_phone() -> None:
    for bad in ("abc", "+1 555 0100", "99890"):
        with pytest.raises(ValidationError):
            CustomerIn(full_name="Ali Valiyev", phone=bad)


def test_customer_in_accepts_valid_uz_phone_and_optional_secondary() -> None:
    c = CustomerIn(full_name="Ali Valiyev", phone="+998 90 123 45 67")
    assert c.phone == "+998901234567"  # (r96) canonicalised on the way in
    assert c.secondary_phone is None
    # a present-but-garbage secondary phone is rejected too
    with pytest.raises(ValidationError):
        CustomerIn(full_name="Ali Valiyev", phone="998901234567", secondary_phone="nope")


def test_guarantor_in_validates_phone() -> None:
    # A bare local number is the same phone as its 998… form and must store as one.
    assert GuarantorIn(full_name="Kafil Aka", phone="901234567").phone == "+998901234567"
    with pytest.raises(ValidationError):
        GuarantorIn(full_name="Kafil Aka", phone="12345")


def test_patches_allow_missing_but_reject_garbage() -> None:
    # an omitted phone is fine on a PATCH; a present garbage phone is not
    assert CustomerPatch().phone is None
    assert GuarantorPatch().phone is None
    with pytest.raises(ValidationError):
        CustomerPatch(phone="abc")
    with pytest.raises(ValidationError):
        GuarantorPatch(phone="+1 555 0100")
