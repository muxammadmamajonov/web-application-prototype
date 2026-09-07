"""MUDD-55 H2 regression: PII masking bypassed via guarantor, KYC scan, risk
score, and the write side of ``/customers/{id}``.

MUDD-25 H2 masked ``_customer_dict`` (passport_*, jshshir, birthdate, address,
monthly_income) for anything below owner/manager, but that masking was only a
view filter on one function — every neighbouring surface that exposes the same
class of PII stayed open on ``main`` (commit ``8295f83``):

1. ``_guarantor_dict`` took no ``role`` and always returned raw passport/jshshir/
   address — a guarantor is the same PII class as a customer, just a different
   table.
2. ``GET /kyc-media`` had no role guard — the passport/ID *scan* behind a masked
   field was one authenticated GET away for any role.
3. ``GET /guarantors`` had no role guard at all (``tenant, _ = ctx``).
4. ``POST /guarantors`` and ``PATCH /guarantors/{gid}`` could write PII with no
   role guard, while ``DELETE /guarantors/{gid}`` already had one — inconsistent
   within the same file.
5. ``GET /risk/score`` (credit-risk signals) had no role guard.
6. ``PATCH /customers/{id}`` had no role guard: sales could not *read* PII but
   could freely *overwrite* it — read/write asymmetry.
7. R2: because ``_customer_dict`` returns the literal string ``"***"`` for a
   masked field, a client that round-trips its own masked GET response into a
   PATCH would silently corrupt the real KYC value with the mask sentinel.

Each test below is red on the pre-fix code (either a 200/201 where a 403 is
required, or a masked field/PII sentinel leaking/persisting) and green after.

--- Review round (blocked PR #951) ---

The first fix for #4/#6 above gated whole endpoints
(``update_customer``/``create_guarantor``/``update_guarantor``) on
owner/manager. That repeated the R1 mistake from MUDD-54: the finding was a
*field*-level PII leak, but the fix was an *endpoint*-level block, which also
403's sales staff out of the legitimate, PII-free parts of those same actions
(fixing a phone number, creating a guarantor during intake). The tests below
that reference "field-level" replace the earlier endpoint-guard tests to
assert the corrected behaviour: sales can still perform the action, but any
PII field in the payload is silently dropped (not written), while a masked
sentinel value continues to be dropped for every role per R2.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

from app.models.enums import TenantRole

# ── unit-level: _guarantor_dict masking (no DB needed) ──────────────────────

def _build_guarantor(**overrides):
    from datetime import UTC, datetime

    from app.models import Guarantor

    defaults = dict(
        full_name="Kafil Aliyev",
        phone="+998901112244",
        passport_series="AB",
        passport_number="7654321",
        jshshir="98765432109876",
        relationship_label="aka",
        telegram_id=None,
        address="Tashkent, Yunusobod",
        photo_urls=["/api/tenant/kyc-media?ref=abc"],
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Guarantor(**defaults)


def test_guarantor_dict_owner_sees_full_pii() -> None:
    from app.api.tenant.customers import _guarantor_dict

    g = _build_guarantor()
    d = _guarantor_dict(g, role=TenantRole.owner)
    assert d["passport_series"] == "AB"
    assert d["passport_number"] == "7654321"
    assert d["jshshir"] == "98765432109876"
    assert d["address"] == "Tashkent, Yunusobod"
    assert d["photo_urls"] == ["/api/tenant/kyc-media?ref=abc"]
    assert d["pii_masked"] is False


def test_guarantor_dict_manager_sees_full_pii() -> None:
    from app.api.tenant.customers import _guarantor_dict

    g = _build_guarantor()
    d = _guarantor_dict(g, role=TenantRole.manager)
    assert d["passport_series"] == "AB"
    assert d["jshshir"] == "98765432109876"


def test_guarantor_dict_sales_sees_masked_pii() -> None:
    """The core H2 #1 bug: _guarantor_dict took no role at all."""
    from app.api.tenant.customers import _guarantor_dict

    g = _build_guarantor()
    d = _guarantor_dict(g, role=TenantRole.sales)
    assert d["passport_series"] == "***"
    assert d["passport_number"] == "***"
    assert d["jshshir"] == "***"
    assert d["address"] == "***"
    assert d["photo_urls"] == []
    assert d["pii_masked"] is True
    # Non-PII fields are unaffected by role.
    assert d["full_name"] == "Kafil Aliyev"
    assert d["phone"] == "+998901112244"


def test_guarantor_dict_no_role_masks_pii_fail_closed() -> None:
    from app.api.tenant.customers import _guarantor_dict

    g = _build_guarantor()
    d = _guarantor_dict(g, role=None)
    assert d["passport_series"] == "***"
    assert d["jshshir"] == "***"


# ── unit-level: masked-sentinel write guard (R2) ─────────────────────────────

def test_is_masked_pii_detects_asterisk_only_values() -> None:
    from app.api.tenant.customers import _is_masked_pii

    assert _is_masked_pii("***") is True
    assert _is_masked_pii("**") is True
    assert _is_masked_pii("*") is True
    assert _is_masked_pii("") is False
    assert _is_masked_pii(None) is False
    assert _is_masked_pii("AB1234") is False
    assert _is_masked_pii("A*B") is False


def test_customer_dict_masked_field_uses_shared_sentinel() -> None:
    """The masked value the GET path emits must be exactly the sentinel the
    PATCH path recognizes, or the round-trip guard silently stops working."""
    from app.api.tenant.customers import _customer_dict, _is_masked_pii
    from tests.test_mudd25_h2_create_customer_pii_masked import _build_customer

    d = _customer_dict(_build_customer(), role=TenantRole.sales)
    assert _is_masked_pii(d["passport_series"])
    assert _is_masked_pii(d["jshshir"])
    assert _is_masked_pii(d["address"])


# ── router-level: role matrix over the DB ────────────────────────────────────

@pytest_asyncio.fixture
async def h2_ctx():
    """Tenant A: owner, manager, sales — one customer, one guarantor. Mirrors
    the ``roles_ctx`` pattern from test_qa004_qa006_tenant_read_role_matrix.py
    plus the domain rows MUDD-55 H2 needs (Customer, Guarantor)."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Customer, Guarantor, Tenant, TenantUser, User

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            tenant = Tenant(slug="mudd55-h2", name="MUDD-55 H2 Tenant")
            s.add(tenant)
            await s.flush()
            owner, manager, sales = (
                User(telegram_id=550001, telegram_username="h2owner"),
                User(telegram_id=550002, telegram_username="h2mgr"),
                User(telegram_id=550003, telegram_username="h2sales"),
            )
            s.add_all([owner, manager, sales])
            await s.flush()
            customer = Customer(
                tenant_id=tenant.id, full_name="Customer Kim",
                phone="+998901230001",
                passport_series="AC", passport_number="1112223",
                jshshir="11122233344455", address="Tashkent, Mirzo Ulugbek",
            )
            guarantor = Guarantor(
                tenant_id=tenant.id, full_name="Guarantor Lee",
                phone="+998901230002",
                passport_series="AD", passport_number="4445556",
                jshshir="22233344455566", address="Tashkent, Sergeli",
            )
            s.add_all([
                TenantUser(tenant_id=tenant.id, user_id=owner.id, role=TenantRole.owner),
                TenantUser(tenant_id=tenant.id, user_id=manager.id, role=TenantRole.manager),
                TenantUser(tenant_id=tenant.id, user_id=sales.id, role=TenantRole.sales),
                customer, guarantor,
            ])
            await s.commit()
            ctx = {
                "tenant_id": str(tenant.id),
                "customer_id": str(customer.id),
                "guarantor_id": str(guarantor.id),
                "owner": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
                "manager": {"Authorization": f"Bearer {issue_token(user_id=str(manager.id))}"},
                "sales": {"Authorization": f"Bearer {issue_token(user_id=str(sales.id))}"},
            }
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, ctx
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _hdr(ctx: dict, role: str) -> dict:
    return {**ctx[role], "X-Tenant-Id": ctx["tenant_id"]}


# H2 #2: GET /kyc-media
@pytest.mark.asyncio
async def test_kyc_media_denies_sales(h2_ctx) -> None:
    client, ctx = h2_ctx
    r = await client.get(
        "/api/tenant/kyc-media", params={"ref": "not-a-real-ref"}, headers=_hdr(ctx, "sales"),
    )
    assert r.status_code == 403, r.text[:200]
    assert r.json()["detail"] == "owner_or_manager_only"


@pytest.mark.asyncio
async def test_kyc_media_allows_owner_past_the_role_guard(h2_ctx) -> None:
    """Owner must clear the new role guard — the 400 further down (bad ref
    content) proves the request reached ``read_kyc_ref``, not that it 403'd."""
    client, ctx = h2_ctx
    r = await client.get(
        "/api/tenant/kyc-media", params={"ref": "not-a-real-ref"}, headers=_hdr(ctx, "owner"),
    )
    assert r.status_code != 403, r.text[:200]


# H2 #1 + #3: GET /guarantors
@pytest.mark.asyncio
async def test_list_guarantors_masks_pii_for_sales(h2_ctx) -> None:
    client, ctx = h2_ctx
    r = await client.get("/api/tenant/guarantors", headers=_hdr(ctx, "sales"))
    assert r.status_code == 200, r.text[:200]
    items = r.json()["items"]
    assert len(items) == 1
    g = items[0]
    assert g["passport_series"] == "***"
    assert g["jshshir"] == "***"
    assert g["address"] == "***"
    assert g["pii_masked"] is True


@pytest.mark.asyncio
async def test_list_guarantors_unmasked_for_owner(h2_ctx) -> None:
    client, ctx = h2_ctx
    r = await client.get("/api/tenant/guarantors", headers=_hdr(ctx, "owner"))
    assert r.status_code == 200, r.text[:200]
    g = r.json()["items"][0]
    assert g["passport_series"] == "AD"
    assert g["jshshir"] == "22233344455566"
    assert g["pii_masked"] is False


# H2 #4: POST/PATCH /guarantors
@pytest.mark.asyncio
async def test_create_guarantor_allows_sales_pii_masked_in_response(h2_ctx) -> None:
    """Sales runs the contract wizard's guarantor step at intake — the same
    class of action as `create_customer` (also unguarded). The write must
    succeed; the PII is protected by masking the response, not by blocking
    the write."""
    client, ctx = h2_ctx
    r = await client.post(
        "/api/tenant/guarantors", headers=_hdr(ctx, "sales"),
        json={"full_name": "New Kafil", "phone": "+998901230009", "passport_series": "AE"},
    )
    assert r.status_code == 201, r.text[:200]
    body = r.json()
    assert body["full_name"] == "New Kafil"
    assert body["passport_series"] == "***"  # written, but masked back to sales
    assert body["pii_masked"] is True


@pytest.mark.asyncio
async def test_create_guarantor_allows_owner(h2_ctx) -> None:
    client, ctx = h2_ctx
    r = await client.post(
        "/api/tenant/guarantors", headers=_hdr(ctx, "owner"),
        json={"full_name": "New Kafil", "phone": "+998901230009", "passport_series": "AE"},
    )
    assert r.status_code == 201, r.text[:200]
    assert r.json()["passport_series"] == "AE"


@pytest.mark.asyncio
async def test_update_guarantor_allows_sales_non_pii_field(h2_ctx) -> None:
    """Sales can still correct a guarantor's relationship/name/phone."""
    client, ctx = h2_ctx
    r = await client.patch(
        f"/api/tenant/guarantors/{ctx['guarantor_id']}", headers=_hdr(ctx, "sales"),
        json={"relationship": "opa"},
    )
    assert r.status_code == 200, r.text[:200]
    assert r.json()["relationship"] == "opa"


@pytest.mark.asyncio
async def test_update_guarantor_drops_pii_field_for_sales(h2_ctx) -> None:
    """Sales PATCHing `address` gets 200 (not 403), but the PII field is
    silently dropped — the DB value is unchanged."""
    client, ctx = h2_ctx
    r = await client.patch(
        f"/api/tenant/guarantors/{ctx['guarantor_id']}", headers=_hdr(ctx, "sales"),
        json={"address": "hijacked", "full_name": "Guarantor Lee Jr"},
    )
    assert r.status_code == 200, r.text[:200]
    assert r.json()["full_name"] == "Guarantor Lee Jr"  # non-PII field applied

    # Confirm on the DB side (via an owner read) that the PII write was dropped.
    r2 = await client.get("/api/tenant/guarantors", headers=_hdr(ctx, "owner"))
    g = next(x for x in r2.json()["items"] if x["id"] == ctx["guarantor_id"])
    assert g["address"] == "Tashkent, Sergeli", g  # unchanged, not "hijacked"


@pytest.mark.asyncio
async def test_update_guarantor_masked_write_is_ignored(h2_ctx) -> None:
    """R2 for guarantors: a masked sentinel round-tripped into a PATCH must not
    overwrite the real passport value, but a real field in the same request
    still applies."""
    client, ctx = h2_ctx
    r = await client.patch(
        f"/api/tenant/guarantors/{ctx['guarantor_id']}", headers=_hdr(ctx, "owner"),
        json={"passport_series": "***", "relationship": "ota"},
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["passport_series"] == "AD", body  # unchanged, not overwritten with "***"
    assert body["relationship"] == "ota"  # unrelated field still updates


# H2 #5: GET /risk/score
@pytest.mark.asyncio
async def test_risk_score_denies_sales(h2_ctx) -> None:
    client, ctx = h2_ctx
    r = await client.get(
        "/api/tenant/risk/score", params={"customer_id": ctx["customer_id"]}, headers=_hdr(ctx, "sales"),
    )
    assert r.status_code == 403, r.text[:200]
    assert r.json()["detail"] == "owner_or_manager_only"


@pytest.mark.asyncio
async def test_risk_score_allows_owner(h2_ctx) -> None:
    client, ctx = h2_ctx
    r = await client.get(
        "/api/tenant/risk/score", params={"customer_id": ctx["customer_id"]}, headers=_hdr(ctx, "owner"),
    )
    assert r.status_code == 200, r.text[:200]


# H2 #6: PATCH /customers/{id} write asymmetry
@pytest.mark.asyncio
async def test_update_customer_allows_sales_non_pii_field(h2_ctx) -> None:
    """Sales can still record outreach notes / fix a phone number — the
    non-PII day-to-day edits this role exists to do."""
    client, ctx = h2_ctx
    r = await client.patch(
        f"/api/tenant/customers/{ctx['customer_id']}", headers=_hdr(ctx, "sales"),
        json={"notes": "called, will pay Friday"},
    )
    assert r.status_code == 200, r.text[:200]
    assert r.json()["notes"] == "called, will pay Friday"


@pytest.mark.asyncio
async def test_update_customer_drops_pii_field_for_sales(h2_ctx) -> None:
    """Sales PATCHing `address`/`passport_number` gets 200 (not 403), but the
    PII fields are silently dropped — the DB value stays whatever owner/
    manager last set. A same-request non-PII field still applies."""
    client, ctx = h2_ctx
    r = await client.patch(
        f"/api/tenant/customers/{ctx['customer_id']}", headers=_hdr(ctx, "sales"),
        json={"address": "hijacked by sales", "passport_number": "9999999", "notes": "still applies"},
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["notes"] == "still applies"

    # Confirm on the DB side (via an owner read) that the PII writes were dropped.
    r2 = await client.get(f"/api/tenant/customers/{ctx['customer_id']}", headers=_hdr(ctx, "owner"))
    c = r2.json()
    assert c["address"] == "Tashkent, Mirzo Ulugbek", c  # unchanged, not "hijacked by sales"
    assert c["passport_number"] == "1112223", c  # unchanged


@pytest.mark.asyncio
async def test_update_customer_allows_owner(h2_ctx) -> None:
    client, ctx = h2_ctx
    r = await client.patch(
        f"/api/tenant/customers/{ctx['customer_id']}", headers=_hdr(ctx, "owner"),
        json={"notes": "called, will pay Friday"},
    )
    assert r.status_code == 200, r.text[:200]
    assert r.json()["notes"] == "called, will pay Friday"


# R2 for customers: masked sentinel must not corrupt real KYC data
@pytest.mark.asyncio
async def test_update_customer_masked_write_is_ignored(h2_ctx) -> None:
    client, ctx = h2_ctx
    r = await client.patch(
        f"/api/tenant/customers/{ctx['customer_id']}", headers=_hdr(ctx, "owner"),
        json={"passport_series": "***", "address": "**", "notes": "still applies"},
    )
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert body["passport_series"] == "AC", body  # unchanged
    assert body["address"] == "Tashkent, Mirzo Ulugbek", body  # unchanged
    assert body["notes"] == "still applies"


# H2 #4: mehnat_daftarchasi_photo_urls must not be advertised to a masked reader
@pytest.mark.asyncio
async def test_customer_dict_hides_kyc_photo_urls_when_masked() -> None:
    from app.api.tenant.customers import _customer_dict
    from tests.test_mudd25_h2_create_customer_pii_masked import _build_customer

    customer = _build_customer(mehnat_daftarchasi_photo_urls=["some-blob-id"])
    masked = _customer_dict(customer, role=TenantRole.sales)
    assert masked["mehnat_daftarchasi_photo_urls"] == []
    unmasked = _customer_dict(customer, role=TenantRole.owner)
    assert unmasked["mehnat_daftarchasi_photo_urls"] != []
