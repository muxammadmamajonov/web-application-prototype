"""MUDD-25 H2 regression: PII masking in ``_customer_dict`` (customers.py).

Two bugs are covered here, both introduced/left over in commit 1c862b8:

1. ``create_customer`` (customers.py:545) called ``_customer_dict(customer)``
   with no ``role`` argument, so newly-created customers came back with EVERY
   PII field masked — even for the owner/manager who just entered the data.
   ``role`` defaulting to ``None`` makes ``can_see_full_pii`` false for
   everyone, which is the opposite of H2's masking intent (mask for
   *low-privilege* roles, not for the owner who is allowed to see it).

2. ``get_customer`` (customers.py:406) and ``update_customer`` (customers.py:579)
   destructured ``tenant, _ = ctx`` and then referenced the discarded
   ``membership`` at the `_customer_dict(c, None, membership.role)` call site —
   a guaranteed ``NameError`` on every single call, so both endpoints 500'd
   unconditionally. That is a crash, not a masking bug, but it was found in
   the same review pass and is exercised by the router-level test below.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.api.tenant.customers import _customer_dict
from app.models import Customer
from app.models.enums import CustomerStatus, TenantRole


def _build_customer(**overrides) -> Customer:
    defaults = dict(
        full_name="Test Customer",
        phone="+998901112233",
        passport_series="AA",
        passport_number="1234567",
        passport_id="AA1234567",
        jshshir="12345678901234",
        birthdate=None,
        monthly_income=Decimal("5000000"),
        address="Tashkent, Chilonzor",
        status=CustomerStatus.active,
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Customer(**defaults)


def test_owner_sees_full_pii() -> None:
    customer = _build_customer()
    d = _customer_dict(customer, role=TenantRole.owner)
    assert d["passport_series"] == "AA"
    assert d["passport_number"] == "1234567"
    assert d["jshshir"] == "12345678901234"
    assert d["monthly_income"] == 5000000
    assert d["address"] == "Tashkent, Chilonzor"


def test_manager_sees_full_pii() -> None:
    customer = _build_customer()
    d = _customer_dict(customer, role=TenantRole.manager)
    assert d["passport_series"] == "AA"
    assert d["jshshir"] == "12345678901234"


def test_sales_sees_masked_pii() -> None:
    customer = _build_customer()
    d = _customer_dict(customer, role=TenantRole.sales)
    assert d["passport_series"] == "***"
    assert d["passport_number"] == "***"
    assert d["passport_id"] == "***"
    assert d["jshshir"] == "***"
    assert d["address"] == "***"
    assert d["monthly_income"] is None
    # Non-PII fields are unaffected by role.
    assert d["full_name"] == "Test Customer"
    assert d["phone"] == "+998901112233"


def test_no_role_masks_pii_fail_closed() -> None:
    """Regression for the exact H2 bug: an omitted role must mask, not leak,
    PII — ``can_see_full_pii`` is false unless role is explicitly owner/manager."""
    customer = _build_customer()
    d = _customer_dict(customer, role=None)
    assert d["passport_series"] == "***"
    assert d["jshshir"] == "***"
    assert d["monthly_income"] is None


async def test_create_customer_returns_pii_for_owner(isolation_ctx) -> None:
    """Router-level check for the specific create_customer bug: the endpoint
    must pass the caller's role into ``_customer_dict`` so an owner sees the
    PII they just entered, instead of the masked defaults ``role=None`` used
    to produce."""
    import pytest

    from tests.conftest import _pg_test_url

    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    resp = await client.post(
        "/api/tenant/customers", headers=auth,
        json={
            "full_name": "Owner Created Customer",
            "phone": "+998907776655",
            "passport_series": "AB",
            "passport_number": "9876543",
        },
    )
    assert resp.status_code == 201, f"{resp.status_code}: {resp.text[:300]}"
    body = resp.json()
    # ctx["auth_a"]'s user is the tenant's owner (see conftest.isolation_ctx).
    assert body["passport_series"] == "AB", body
    assert body["passport_number"] == "9876543", body


async def test_get_and_update_customer_do_not_crash(isolation_ctx) -> None:
    """Router-level check for the NameError in get_customer/update_customer:
    both must return 200, not 500."""
    import pytest

    from tests.conftest import _pg_test_url

    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    created = await client.post(
        "/api/tenant/customers", headers=auth,
        json={"full_name": "Get/Update Target", "phone": "+998901239876"},
    )
    assert created.status_code == 201, created.text[:300]
    cid = created.json()["id"]

    got = await client.get(f"/api/tenant/customers/{cid}", headers=auth)
    assert got.status_code == 200, f"GET crashed: {got.status_code} {got.text[:300]}"

    patched = await client.patch(
        f"/api/tenant/customers/{cid}", headers=auth,
        json={"notes": "updated"},
    )
    assert patched.status_code == 200, f"PATCH crashed: {patched.status_code} {patched.text[:300]}"
    assert patched.json()["notes"] == "updated"
