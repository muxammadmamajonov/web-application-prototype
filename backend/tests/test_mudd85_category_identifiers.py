"""MUDD-85 Phase 2 over the wire: category-linked identifier fields.

`catalog_categories.identifier_schema` already declared, per category, which
per-unit fields a seller should capture at sale time (IMEI 1/2 for phones,
VIN/engine number for cars) — but nothing ever read it. These tests hold the
behaviour that makes reading it worth doing: a required field blocks a sale
until it is filled in, an unknown field is refused rather than silently
stored, and a duplicate value 400s instead of just landing in two contracts —
per the gate decision (MUDD-94, Example User, 2026-08-27T18:22:53Z): a
duplicate identifier is a hard 400, not a warning.

(MUDD-123) That decision's third answer — "old contracts must be filled in
too" — is the one thing here that has been revised. It was taken about NEW
required-ness and never measured against rows that already existed; once
`r96catidschema` applied it retroactively to 114 categories with no backfill,
it stopped meaning "fill it in" and started meaning "this contract can never be
edited again". The last section of this file holds the corrected boundary: an
existing line keeps what it has, everything else still has to earn it.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

INSTALLMENTS = "/api/tenant/installments"


@pytest.fixture
def _no_telegram(monkeypatch):
    async def _noop(*_a, **_kw):
        return None

    from app.services import customer_outreach
    monkeypatch.setattr(customer_outreach, "notify_new_contract", _noop)


@pytest_asyncio.fixture
async def ctx(_no_telegram):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import (
        CatalogCategory,
        Customer,
        Tenant,
        TenantSettings,
        TenantUser,
        User,
    )
    from app.models.enums import CustomerStatus, TenantRole

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            t = Tenant(slug="ident-shop", name="Ident Shop")
            s.add(t); await s.flush()
            s.add(TenantSettings(tenant_id=t.id, currency="UZS"))
            owner = User(telegram_id=880101)
            s.add(owner); await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
            c = Customer(tenant_id=t.id, full_name="Buyer", phone="+998901234701",
                         status=CustomerStatus.active)
            s.add(c); await s.flush()

            # A second, isolated tenant — proves uniqueness is per-tenant, not
            # global.
            t2 = Tenant(slug="other-shop", name="Other Shop")
            s.add(t2); await s.flush()
            s.add(TenantSettings(tenant_id=t2.id, currency="UZS"))
            owner2 = User(telegram_id=880102)
            s.add(owner2); await s.flush()
            s.add(TenantUser(tenant_id=t2.id, user_id=owner2.id, role=TenantRole.owner))
            c2 = Customer(tenant_id=t2.id, full_name="Buyer 2", phone="+998901234702",
                          status=CustomerStatus.active)
            s.add(c2); await s.flush()

            phones = CatalogCategory(
                slug="smartfonlar", name_uz="Smartfonlar", name_ru="Смартфоны",
                business_types=["electronics"],
                identifier_schema=[
                    {"key": "imei1", "label_uz": "IMEI 1", "label_ru": "IMEI 1",
                     "type": "text", "required": True, "max_len": 17},
                    {"key": "imei2", "label_uz": "IMEI 2", "label_ru": "IMEI 2",
                     "type": "text", "required": False, "max_len": 17},
                ],
            )
            # (MUDD-123) A category whose required field carries a real
            # `validator`, so the grandfathering tests can exercise a STORED
            # value that today's rules reject — not just a missing one.
            cars = CatalogCategory(
                slug="avtomobillar", name_uz="Avtomobillar", name_ru="Автомобили",
                business_types=["auto"],
                identifier_schema=[
                    {"key": "vin", "label_uz": "VIN", "label_ru": "VIN",
                     "type": "text", "required": True, "validator": "vin",
                     "min_len": 17, "max_len": 17},
                ],
            )
            no_schema = CatalogCategory(
                slug="boshqa-mahsulotlar", name_uz="Boshqa", name_ru="Другое",
                business_types=[],
            )
            s.add_all([phones, cars, no_schema]); await s.flush()

            data = {
                "customer_id": str(c.id),
                "other_tenant_customer_id": str(c2.id),
                "session": Session,
                "auth": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
                "other_auth": {"Authorization": f"Bearer {issue_token(user_id=str(owner2.id))}"},
            }
            await s.commit()
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _body(d, *items: dict, customer_id: str | None = None) -> dict:
    return {
        "customer_id": customer_id or d["customer_id"], "months": 6, "down_payment": "0",
        "items": [{"product_name_snapshot": "Mahsulot", "qty": 1,
                   "unit_price": "1000000", **it} for it in items],
    }


async def _create(client, d, *items: dict, auth=None, customer_id=None):
    return await client.post(
        INSTALLMENTS, headers=auth or d["auth"],
        json=_body(d, *items, customer_id=customer_id),
    )


async def _get(client, d, contract_id, *, auth=None) -> dict:
    r = await client.get(f"{INSTALLMENTS}/{contract_id}", headers=auth or d["auth"])
    return r.json()


VALID_IMEI1 = {"category": "smartfonlar", "identifiers": {"imei1": "352099001761481"}}


async def test_missing_required_identifier_is_rejected(ctx) -> None:
    client, d = ctx
    r = await _create(client, d, {"category": "smartfonlar"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "category_identifier_required:imei1"


async def test_valid_required_identifier_is_stored_and_returned(ctx) -> None:
    client, d = ctx
    r = await _create(client, d, VALID_IMEI1)
    assert r.status_code in (200, 201), r.text
    detail = await _get(client, d, r.json()["id"])
    assert detail["items"][0]["identifiers"] == {"imei1": "352099001761481"}


async def test_optional_field_may_be_omitted(ctx) -> None:
    """IMEI 2 is optional — a submission with only IMEI 1 is complete."""
    client, d = ctx
    r = await _create(client, d, VALID_IMEI1)
    assert r.status_code in (200, 201), r.text
    detail = await _get(client, d, r.json()["id"])
    assert "imei2" not in detail["items"][0]["identifiers"]


async def test_optional_field_is_stored_when_given(ctx) -> None:
    client, d = ctx
    r = await _create(client, d, {
        "category": "smartfonlar",
        "identifiers": {"imei1": "352099001761481", "imei2": "490154203237518"},
    })
    assert r.status_code in (200, 201), r.text
    detail = await _get(client, d, r.json()["id"])
    assert detail["items"][0]["identifiers"] == {
        "imei1": "352099001761481", "imei2": "490154203237518",
    }


async def test_unknown_identifier_key_is_rejected(ctx) -> None:
    client, d = ctx
    r = await _create(client, d, {
        "category": "smartfonlar",
        "identifiers": {"imei1": "352099001761481", "vin": "SOMEVIN"},
    })
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "category_identifier_unknown_key:vin"


async def test_a_field_not_in_any_schema_is_rejected_for_uncategorised_items(ctx) -> None:
    """No category → no schema → every submitted key is unknown."""
    client, d = ctx
    r = await _create(client, d, {"identifiers": {"imei1": "352099001761481"}})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "category_identifier_unknown_key:imei1"


async def test_a_category_with_an_empty_schema_rejects_any_identifier(ctx) -> None:
    client, d = ctx
    r = await _create(client, d, {
        "category": "boshqa-mahsulotlar", "identifiers": {"imei1": "352099001761481"},
    })
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "category_identifier_unknown_key:imei1"


async def test_value_over_the_schemas_max_len_is_rejected(ctx) -> None:
    client, d = ctx
    r = await _create(client, d, {
        "category": "smartfonlar", "identifiers": {"imei1": "1" * 18},
    })
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "category_identifier_too_long:imei1"


async def test_duplicate_identifier_within_the_same_tenant_is_a_400(ctx) -> None:
    """The gate decision: a duplicate IMEI 400s — it is not just a warning
    like the legacy `serial_number` index."""
    client, d = ctx
    first = await _create(client, d, VALID_IMEI1)
    assert first.status_code in (200, 201), first.text

    second = await _create(client, d, VALID_IMEI1)
    assert second.status_code == 400, second.text
    assert second.json()["detail"] == "category_identifier_duplicate:imei1"


async def test_duplicate_identifier_within_one_submission_is_a_400(ctx) -> None:
    """Two lines in the SAME contract both claiming the same IMEI 1 — caught
    before either reaches the database."""
    client, d = ctx
    r = await _create(client, d, VALID_IMEI1, VALID_IMEI1)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "category_identifier_duplicate:imei1"


async def test_same_identifier_value_is_allowed_across_different_tenants(ctx) -> None:
    """Uniqueness is per-tenant, not platform-wide."""
    client, d = ctx
    first = await _create(client, d, VALID_IMEI1)
    assert first.status_code in (200, 201), first.text

    second = await _create(
        client, d, VALID_IMEI1,
        auth=d["other_auth"], customer_id=d["other_tenant_customer_id"],
    )
    assert second.status_code in (200, 201), second.text


# ── MUDD-123: what a retroactively-required field may and may not demand ──────
#
# The test that used to sit here — `..._missing_the_new_required_field_is_blocked`
# — pinned the opposite of every assertion below: it asserted that editing a
# pre-feature contract 400s until an IMEI is supplied. That was a real decision
# (MUDD-94 gate, answer B: "old contracts must be filled in too"), but it was
# taken about NEW required-ness, before anything measured what it does to rows
# that already exist. `r96catidschema` then made a field required on all 114
# categories at once, retroactively and with no backfill, while `rk0catident`
# had created `installment_item_identifiers` EMPTY — so "fill it in too" turned
# into "this contract can never be edited again", including to fix a typo in a
# customer's name. The value cannot be looked up: the phone left with the
# customer months ago, and the only way to satisfy the old rule was to invent an
# IMEI, which is worse than the blank it replaces.
#
# Deliberately NOT reverted: a NEW contract, and a NEW line added to an old one,
# still must carry the identifier — see the two `..._still_required` tests. The
# relief is scoped to the exact line being replaced, and only to what that line
# was never given a chance to record. Full reasoning: `docs/CATALOG_IDENTIFIERS.md`.

#: 17 chars containing the ISO-3779-excluded letter `O` — accepted by the loose
#: r55-era schema, rejected by the `validator: "vin"` r96 attached.
LEGACY_BAD_VIN = "JTDBOB3U40J123456"
VALID_VIN = "JTDBR32E430123456"


async def _legacy_contract(d, *lines: dict) -> str:
    """A contract written straight to the database, bypassing the API.

    That is the point: the API would already enforce today's rules, and these
    tests are about rows that predate them. `lines` are
    `{"name", "category", "identifiers"?}` dicts.
    """
    import datetime

    from sqlalchemy import select as sa_select

    from app.models import Customer as _Customer
    from app.models import Installment, InstallmentItem, InstallmentItemIdentifier
    from app.models.enums import InstallmentStatus

    async with d["session"]() as s:
        cust = (await s.execute(
            sa_select(_Customer).where(_Customer.id == d["customer_id"])
        )).scalar_one()
        legacy = Installment(
            tenant_id=cust.tenant_id, customer_id=cust.id,
            status=InstallmentStatus.active, currency="UZS",
            total_amount=1_000_000, down_payment=0, principal=1_000_000,
            months=6, monthly_amount=166_667, paid_amount=0,
            start_date=datetime.date.today(),
        )
        s.add(legacy); await s.flush()
        for line in lines:
            item = InstallmentItem(
                installment_id=legacy.id, product_name_snapshot=line["name"],
                category=line["category"], qty=1,
                unit_price=1_000_000, line_total=1_000_000,
            )
            s.add(item); await s.flush()
            for key, value in line.get("identifiers", {}).items():
                s.add(InstallmentItemIdentifier(
                    tenant_id=cust.tenant_id, item_id=item.id, key=key, value=value,
                ))
        await s.commit()
        return str(legacy.id)


async def test_editing_an_old_contract_missing_the_new_required_field_is_allowed(ctx) -> None:
    """The headline fix: a pre-feature line with no identifiers at all can still
    be saved, and saving it does not invent one."""
    client, d = ctx
    legacy_id = await _legacy_contract(
        d, {"name": "Eski shartnoma", "category": "smartfonlar"},
    )

    r = await client.put(
        f"{INSTALLMENTS}/{legacy_id}", headers=d["auth"],
        json=_body(d, {"category": "smartfonlar", "product_name_snapshot": "Eski shartnoma"}),
    )
    assert r.status_code in (200, 201), r.text
    detail = await _get(client, d, legacy_id)
    assert detail["items"][0]["identifiers"] == {}


async def test_editing_an_unrelated_field_on_an_old_contract_passes(ctx) -> None:
    """The case the owner actually hits: fix the term/notes, touch no identifier."""
    client, d = ctx
    legacy_id = await _legacy_contract(
        d, {"name": "Eski shartnoma", "category": "smartfonlar"},
    )

    body = _body(d, {"category": "smartfonlar", "product_name_snapshot": "Eski shartnoma"})
    body["months"] = 9
    body["notes"] = "Mijoz ismidagi xato tuzatildi"
    r = await client.put(f"{INSTALLMENTS}/{legacy_id}", headers=d["auth"], json=body)
    assert r.status_code in (200, 201), r.text
    assert r.json()["months"] == 9


async def test_supplying_a_new_value_for_the_never_captured_field_is_still_validated(ctx) -> None:
    """Relief is for the ABSENCE only. Fill the field in and it faces today's
    rules like any other value."""
    client, d = ctx
    legacy_id = await _legacy_contract(d, {"name": "Eski avto", "category": "avtomobillar"})

    bad = await client.put(
        f"{INSTALLMENTS}/{legacy_id}", headers=d["auth"],
        json=_body(d, {"category": "avtomobillar", "product_name_snapshot": "Eski avto",
                       "identifiers": {"vin": LEGACY_BAD_VIN}}),
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["detail"] == "catalog_identifier_vin_charset:vin"

    ok = await client.put(
        f"{INSTALLMENTS}/{legacy_id}", headers=d["auth"],
        json=_body(d, {"category": "avtomobillar", "product_name_snapshot": "Eski avto",
                       "identifiers": {"vin": VALID_VIN}}),
    )
    assert ok.status_code in (200, 201), ok.text
    detail = await _get(client, d, legacy_id)
    assert detail["items"][0]["identifiers"] == {"vin": VALID_VIN}


async def test_a_stored_value_todays_validator_rejects_survives_an_unchanged_resubmit(ctx) -> None:
    """The edit form echoes stored identifiers back verbatim. A VIN recorded
    under the looser r55 schema must not turn every later edit into a 400."""
    client, d = ctx
    legacy_id = await _legacy_contract(
        d, {"name": "Eski avto", "category": "avtomobillar",
            "identifiers": {"vin": LEGACY_BAD_VIN}},
    )
    detail = await _get(client, d, legacy_id)
    assert detail["items"][0]["identifiers"] == {"vin": LEGACY_BAD_VIN}

    r = await client.put(
        f"{INSTALLMENTS}/{legacy_id}", headers=d["auth"],
        json=_body(d, {"category": "avtomobillar", "product_name_snapshot": "Eski avto",
                       "identifiers": {"vin": LEGACY_BAD_VIN}}),
    )
    assert r.status_code in (200, 201), r.text
    after = await _get(client, d, legacy_id)
    assert after["items"][0]["identifiers"] == {"vin": LEGACY_BAD_VIN}


async def test_changing_a_grandfathered_value_must_satisfy_todays_validator(ctx) -> None:
    """Unchanged is forgiven; changed is not. The other direction of the same rule."""
    client, d = ctx
    legacy_id = await _legacy_contract(
        d, {"name": "Eski avto", "category": "avtomobillar",
            "identifiers": {"vin": LEGACY_BAD_VIN}},
    )

    r = await client.put(
        f"{INSTALLMENTS}/{legacy_id}", headers=d["auth"],
        json=_body(d, {"category": "avtomobillar", "product_name_snapshot": "Eski avto",
                       "identifiers": {"vin": "JTDBOB3U40J999999"}}),
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "catalog_identifier_vin_charset:vin"


async def test_a_new_contract_still_requires_the_identifier(ctx) -> None:
    """MUDD-113 is intact — nothing about the edit-path relief reaches create."""
    client, d = ctx
    r = await _create(client, d, {"category": "avtomobillar"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "category_identifier_required:vin"


async def test_a_line_added_by_the_edit_still_requires_the_identifier(ctx) -> None:
    """The relief is per replaced line, not per contract: an old contract is not
    a licence to write new identifier-less lines into it."""
    client, d = ctx
    legacy_id = await _legacy_contract(
        d, {"name": "Eski shartnoma", "category": "smartfonlar"},
    )

    r = await client.put(
        f"{INSTALLMENTS}/{legacy_id}", headers=d["auth"],
        json=_body(
            d,
            {"category": "smartfonlar", "product_name_snapshot": "Eski shartnoma"},
            {"category": "smartfonlar", "product_name_snapshot": "Yangi qo'shilgan"},
        ),
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "category_identifier_required:imei1"


async def test_deleting_a_stored_required_identifier_is_refused(ctx) -> None:
    """Never captured is forgiven. Captured and then blanked is a deletion, and
    the field is required."""
    client, d = ctx
    legacy_id = await _legacy_contract(
        d, {"name": "Eski avto", "category": "avtomobillar",
            "identifiers": {"vin": VALID_VIN}},
    )

    r = await client.put(
        f"{INSTALLMENTS}/{legacy_id}", headers=d["auth"],
        json=_body(d, {"category": "avtomobillar", "product_name_snapshot": "Eski avto"}),
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "category_identifier_required:vin"


async def test_a_grandfathered_value_is_still_refused_as_a_duplicate(ctx) -> None:
    """Old does not mean exempt from uniqueness.

    Line A resubmits its stored VIN unchanged, so it skips the validator pass —
    but it rejoins BEFORE the duplicate checks, so line B claiming the same
    number is still caught. Skipping validation must not also mean skipping
    "is this already somebody else's device".
    """
    client, d = ctx
    legacy_id = await _legacy_contract(
        d,
        {"name": "Avto A", "category": "avtomobillar", "identifiers": {"vin": VALID_VIN}},
        {"name": "Avto B", "category": "avtomobillar"},
    )

    r = await client.put(
        f"{INSTALLMENTS}/{legacy_id}", headers=d["auth"],
        json=_body(
            d,
            {"category": "avtomobillar", "product_name_snapshot": "Avto A",
             "identifiers": {"vin": VALID_VIN}},
            {"category": "avtomobillar", "product_name_snapshot": "Avto B",
             "identifiers": {"vin": VALID_VIN}},
        ),
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "category_identifier_duplicate:vin"


async def test_a_stored_key_the_schema_no_longer_declares_is_dropped_not_rejected(ctx) -> None:
    """r96 REPLACED r55's schemas, so a line saved between them carries keys that
    no longer exist. The edit form echoes them back; refusing them would re-block
    the same edits this fix unblocks. They are dropped, not re-persisted."""
    client, d = ctx
    legacy_id = await _legacy_contract(
        d, {"name": "Eski avto", "category": "avtomobillar",
            "identifiers": {"vin": VALID_VIN, "kuzov": "ESKI-KALIT-1"}},
    )

    r = await client.put(
        f"{INSTALLMENTS}/{legacy_id}", headers=d["auth"],
        json=_body(d, {"category": "avtomobillar", "product_name_snapshot": "Eski avto",
                       "identifiers": {"vin": VALID_VIN, "kuzov": "ESKI-KALIT-1"}}),
    )
    assert r.status_code in (200, 201), r.text
    detail = await _get(client, d, legacy_id)
    assert detail["items"][0]["identifiers"] == {"vin": VALID_VIN}
