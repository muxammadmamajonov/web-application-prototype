"""FB-006 over the wire: the API enforces the identifier rules, the migration
classifies existing rows without reinterpreting them.

The unit rules live in `test_fb006_product_identifier_semantics`. This is the
part that only a real request and a real migration can show: that a client
cannot bypass the rules, that the stored value is the normalised one the
duplicate check will compare, and that a legacy 15-digit string is labelled
`LEGACY_UNVERIFIED` rather than promoted to an IMEI claim.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

INSTALLMENTS = "/api/tenant/installments"
VALID_IMEI = "352099001761481"


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
    from app.models import Customer, Tenant, TenantSettings, TenantUser, User
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
            owner = User(telegram_id=770001)
            s.add(owner); await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
            c = Customer(tenant_id=t.id, full_name="Buyer", phone="+998901234501",
                         status=CustomerStatus.active)
            s.add(c); await s.flush()
            data = {"tenant_id": str(t.id), "customer_id": str(c.id), "session": Session,
                    "auth": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"}}
            await s.commit()
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _body(d, item: dict) -> dict:
    return {
        "customer_id": d["customer_id"], "months": 6, "down_payment": "0",
        "items": [{"product_name_snapshot": "Telefon", "qty": 1,
                   "unit_price": "6000000", **item}],
    }


async def _detail(client, d, contract_id) -> dict:
    return (await client.get(f"{INSTALLMENTS}/{contract_id}", headers=d["auth"])).json()


# ── what the API accepts ────────────────────────────────────────────────────

async def test_a_valid_imei_is_stored_with_its_declared_kind(ctx) -> None:
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"],
                          json=_body(d, {"serial_number": VALID_IMEI, "identifier_type": "IMEI"}))
    assert r.status_code in (200, 201), r.text
    item = (await _detail(client, d, r.json()["id"]))["items"][0]
    assert item["serial_number"] == VALID_IMEI
    assert item["identifier_type"] == "IMEI"


async def test_a_scanned_imei_is_normalised_before_it_is_stored(ctx) -> None:
    """What is stored must be what the duplicate check compares."""
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"],
                          json=_body(d, {"serial_number": "35-209900-176148-1",
                                         "identifier_type": "IMEI"}))
    assert r.status_code in (200, 201), r.text
    assert (await _detail(client, d, r.json()["id"]))["items"][0]["serial_number"] == VALID_IMEI


async def test_a_serial_keeps_its_case_and_is_trimmed(ctx) -> None:
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"],
                          json=_body(d, {"serial_number": "  SN-2026/0142  ",
                                         "identifier_type": "SERIAL_OR_CONTRACT"}))
    assert r.status_code in (200, 201), r.text
    assert (await _detail(client, d, r.json()["id"]))["items"][0]["serial_number"] == "SN-2026/0142"


async def test_a_line_with_no_identifier_is_accepted(ctx) -> None:
    """Most sales are not of serialised goods."""
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d, {}))
    assert r.status_code in (200, 201), r.text
    item = (await _detail(client, d, r.json()["id"]))["items"][0]
    assert item["serial_number"] is None
    assert item["identifier_type"] is None


# ── what the API refuses ────────────────────────────────────────────────────

@pytest.mark.parametrize(("item", "reason"), [
    ({"serial_number": "35209900176148", "identifier_type": "IMEI"}, "length"),
    ({"serial_number": "352099001761482", "identifier_type": "IMEI"}, "checksum"),
    ({"serial_number": "35209900176148A", "identifier_type": "IMEI"}, "letters"),
    ({"serial_number": "", "identifier_type": "IMEI"}, "blank with a declared kind"),
    ({"serial_number": "<script>x</script>", "identifier_type": "SERIAL_OR_CONTRACT"}, "markup"),
    ({"serial_number": "x" * 65, "identifier_type": "SERIAL_OR_CONTRACT"}, "too long"),
    ({"serial_number": VALID_IMEI}, "value with no declared kind"),
    ({"serial_number": "SN-1", "identifier_type": "PASSPORT"}, "unknown kind"),
    ({"serial_number": VALID_IMEI, "identifier_type": "LEGACY_UNVERIFIED"}, "legacy is not selectable"),
])
async def test_an_invalid_identifier_is_refused(ctx, item, reason) -> None:
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d, item))
    assert r.status_code == 422, f"{reason}: {r.status_code} {r.text}"


async def test_an_unknown_item_field_cannot_be_smuggled_in(ctx) -> None:
    """Mass assignment: an unlisted key is refused, not quietly ignored."""
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"],
                          json=_body(d, {"line_total": "1", "installment_id": "x"}))
    assert r.status_code == 422, r.text


async def test_nothing_is_persisted_when_the_identifier_is_rejected(ctx) -> None:
    from sqlalchemy import func, select

    from app.models import Installment

    client, d = ctx
    await client.post(INSTALLMENTS, headers=d["auth"],
                      json=_body(d, {"serial_number": "352099001761482",
                                     "identifier_type": "IMEI"}))
    async with d["session"]() as s:
        assert (await s.execute(select(func.count(Installment.id)))).scalar_one() == 0


# ── the database is the backstop ────────────────────────────────────────────

async def test_the_database_refuses_an_unknown_identifier_kind(ctx) -> None:
    from decimal import Decimal

    from sqlalchemy.exc import IntegrityError

    from app.models import InstallmentItem

    client, d = ctx
    created = (await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d, {}))).json()
    import uuid
    async with d["session"]() as s:
        s.add(InstallmentItem(
            installment_id=uuid.UUID(created["id"]), product_name_snapshot="X",
            serial_number="SN-1", identifier_type="PASSPORT",
            qty=1, unit_price=Decimal("1"), line_total=Decimal("1"),
        ))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_the_database_refuses_a_value_without_a_kind(ctx) -> None:
    from decimal import Decimal

    from sqlalchemy.exc import IntegrityError

    from app.models import InstallmentItem

    client, d = ctx
    created = (await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d, {}))).json()
    import uuid
    async with d["session"]() as s:
        s.add(InstallmentItem(
            installment_id=uuid.UUID(created["id"]), product_name_snapshot="X",
            serial_number="SN-1", identifier_type=None,
            qty=1, unit_price=Decimal("1"), line_total=Decimal("1"),
        ))
        with pytest.raises(IntegrityError):
            await s.commit()


# ── the duplicate warning still works, still tenant-scoped ──────────────────

async def test_the_duplicate_check_matches_the_normalised_value(ctx) -> None:
    """A scanned label and a typed IMEI are the same device to the warning."""
    client, d = ctx
    await client.post(INSTALLMENTS, headers=d["auth"],
                      json=_body(d, {"serial_number": VALID_IMEI, "identifier_type": "IMEI"}))
    r = await client.get("/api/tenant/imei-check", headers=d["auth"],
                         params={"serial": VALID_IMEI})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["in_tenant"] is True
    assert body["cross_tenant_count"] == 0


async def test_no_global_uniqueness_constraint_was_added(ctx) -> None:
    """The same serial legitimately recurs across shops; a unique index would
    reject honest data. Duplicates stay a warning."""
    from sqlalchemy import text

    _client, d = ctx
    async with d["session"]() as s:
        rows = (await s.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'installment_items'"
        ))).scalars().all()
    serial_indexes = [r for r in rows if "serial_number" in r]
    assert serial_indexes, "the duplicate scan needs an index"
    assert not any("UNIQUE" in r.upper() for r in serial_indexes), serial_indexes


# ── the migration's legacy classification ───────────────────────────────────

def test_the_migration_classifies_legacy_rows_without_reinterpreting_them() -> None:
    """A 15-digit legacy string is NOT promoted to an IMEI claim."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1]
           / "alembic/versions/rb0_item_identifier_type.py").read_text()
    assert "LEGACY_UNVERIFIED" in src
    # Nothing in the migration may look at the VALUE to decide the kind.
    for smell in ("length(", "~ '^[0-9]", "luhn", "= 15", "char_length"):
        assert smell not in src.lower(), smell
    # And it must not rewrite the stored value.
    assert "SET serial_number" not in src
