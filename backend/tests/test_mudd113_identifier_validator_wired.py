"""MUDD-113 — `validate_catalog_identifier` is actually wired into the
installment write path, not just callable in isolation.

`test_mudd85_category_identifiers.py` proved the required/max_len/unknown-key
gate at the API layer; its `ctx` fixture's schema carries no `validator` key,
so a bad-Luhn IMEI or a bad-charset VIN was accepted there right up until
`_validate_item_identifiers` (`app/api/tenant/installments.py`) started
calling `validate_catalog_identifier` for fields that declare one. These
tests exercise that wiring over a real HTTP request — not the pure function
directly — with a validator-carrying schema shaped like the one
`r96_seed_identifier_validators.py` actually seeds.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

INSTALLMENTS = "/api/tenant/installments"

VALID_IMEI = "352099001761481"           # correct GSMA Luhn check digit
VALID_VIN = "1HGCM82633A004352"          # 17 chars, no I/O/Q


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
    from app.models import CatalogCategory, Customer, Tenant, TenantSettings, TenantUser, User
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
            t = Tenant(slug="ident-validator-shop", name="Ident Validator Shop")
            s.add(t); await s.flush()
            s.add(TenantSettings(tenant_id=t.id, currency="UZS"))
            owner = User(telegram_id=880201)
            s.add(owner); await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
            c = Customer(tenant_id=t.id, full_name="Buyer", phone="+998901234801",
                         status=CustomerStatus.active)
            s.add(c); await s.flush()

            phones = CatalogCategory(
                slug="smartfonlar", name_uz="Smartfonlar", name_ru="Смартфоны",
                business_types=["electronics"],
                identifier_schema=[
                    {"key": "imei1", "label_uz": "IMEI 1", "label_ru": "IMEI 1",
                     "type": "text", "required": True, "min_len": 15, "max_len": 15,
                     "validator": "imei"},
                    {"key": "imei2", "label_uz": "IMEI 2", "label_ru": "IMEI 2",
                     "type": "text", "required": False, "min_len": 15, "max_len": 15,
                     "validator": "imei"},
                ],
            )
            cars = CatalogCategory(
                slug="avtomobillar", name_uz="Avtomobillar", name_ru="Автомобили",
                business_types=["auto"],
                identifier_schema=[
                    {"key": "vin", "label_uz": "VIN", "label_ru": "VIN",
                     "type": "text", "required": True, "min_len": 17, "max_len": 17,
                     "validator": "vin"},
                ],
            )
            s.add_all([phones, cars]); await s.flush()

            data = {
                "customer_id": str(c.id),
                "auth": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
            }
            await s.commit()
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _body(d, *items: dict) -> dict:
    return {
        "customer_id": d["customer_id"], "months": 6, "down_payment": "0",
        "items": [{"product_name_snapshot": "Mahsulot", "qty": 1,
                   "unit_price": "1000000", **it} for it in items],
    }


async def _create(client, d, *items: dict):
    return await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d, *items))


async def test_a_bad_luhn_imei_is_rejected_over_the_wire(ctx) -> None:
    client, d = ctx
    bad_imei = VALID_IMEI[:-1] + str((int(VALID_IMEI[-1]) + 1) % 10)
    r = await _create(client, d, {"category": "smartfonlar", "identifiers": {"imei1": bad_imei}})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "catalog_identifier_imei_checksum:imei1"


async def test_a_valid_imei_is_accepted_over_the_wire(ctx) -> None:
    client, d = ctx
    r = await _create(client, d, {"category": "smartfonlar", "identifiers": {"imei1": VALID_IMEI}})
    assert r.status_code in (200, 201), r.text


async def test_a_bad_charset_vin_is_rejected_over_the_wire(ctx) -> None:
    client, d = ctx
    poisoned_vin = "I" + VALID_VIN[1:]  # 17 chars — length check can't mask this
    r = await _create(client, d, {"category": "avtomobillar", "identifiers": {"vin": poisoned_vin}})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "catalog_identifier_vin_charset:vin"


async def test_an_optional_field_of_only_invisible_characters_is_not_a_500(ctx) -> None:
    """(B1 regression) `imei2` is optional. `_normalize` (category level) does
    NOT strip a zero-width space, so a value made of nothing else looks
    non-blank there and reaches `validate_catalog_identifier` — whose
    `normalize_imei` DOES strip it, collapsing to `""` and returning
    `(None, None)`, the "nothing was really captured" outcome for an optional
    field. Before the fix this hit `assert checked_value is not None` and
    the request 500'd instead of succeeding with `imei2` simply absent."""
    client, d = ctx
    r = await _create(client, d, {
        "category": "smartfonlar",
        "identifiers": {"imei1": VALID_IMEI, "imei2": "​"},
    })
    assert r.status_code in (200, 201), r.text
    detail = (await client.get(f"{INSTALLMENTS}/{r.json()['id']}", headers=d["auth"])).json()
    assert "imei2" not in detail["items"][0]["identifiers"]
    assert detail["items"][0]["identifiers"]["imei1"] == VALID_IMEI


async def test_a_valid_vin_is_stored_upper_cased_and_canonical(ctx) -> None:
    """The validator's normalized output replaces the raw submission — a
    lower-cased VIN is still accepted, but the stored/returned value is the
    canonical upper-cased form, matching what a VIN scanner/label shows."""
    client, d = ctx
    r = await _create(client, d, {"category": "avtomobillar", "identifiers": {"vin": VALID_VIN.lower()}})
    assert r.status_code in (200, 201), r.text
    detail = (await client.get(f"{INSTALLMENTS}/{r.json()['id']}", headers=d["auth"])).json()
    assert detail["items"][0]["identifiers"]["vin"] == VALID_VIN
