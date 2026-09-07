"""A contract keeps the currency it was written in, forever.

THE DEFECT (founder beta, 2026-08-09). `tenant_settings.currency` was the only
currency in the schema, and every money label re-read it at render time. A shop
that switched UZS → USD relabelled its own history: contracts already signed in
so'm — and the receipts already handed to customers against them — started
printing USD. Same number, different debt, no conversion, no record.

THE POLICY (founder). Changing the shop's currency affects NEW contracts only.
Old contracts keep theirs. There is no FX anywhere: nothing in this file changes
an amount, and the tests assert that too.

WHAT IS PINNED HERE is the whole scenario end to end — create under UZS, switch
the shop to USD, and check that the old contract is still so'm on every path a
person can reach it through (merchant list, merchant detail, customer list,
customer detail, customer summary, payment rows, contract PDF context, receipt
PDF context, dunning message), while a contract created after the switch is USD
on all the same paths.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

INSTALLMENTS = "/api/tenant/installments"
PAYMENTS = "/api/tenant/payments"
SETTINGS = "/api/tenant/settings"


@pytest.fixture
def telegram(monkeypatch):
    """Capture outbound Telegram text instead of sending it.

    The customer here is deliberately Telegram-linked (that is the path a
    reminder takes), so every send has to be intercepted at the service
    boundary. Returning the captured bodies also gives the reminder test the
    real rendered message rather than a re-render of the template.
    """
    sent: list[str] = []

    async def _send(chat_id, text, **_kw):
        sent.append(text)
        return True

    async def _notify_new_contract(*_a, **_kw):
        return None

    from app.services import customer_outreach, notifications
    monkeypatch.setattr(notifications, "send_to_telegram", _send)
    monkeypatch.setattr(customer_outreach, "notify_new_contract", _notify_new_contract)
    return sent


@pytest_asyncio.fixture
async def ctx(telegram, grant_verified_phone):
    """One shop set to UZS, one customer, an owner, and a linked Mini App user."""
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
            t = Tenant(slug="fx-shop", name="FX Shop")
            s.add(t); await s.flush()
            s.add(TenantSettings(tenant_id=t.id, currency="UZS"))
            owner = User(telegram_id=440001)
            shopper = User(telegram_id=440002)
            grant_verified_phone(shopper)
            s.add_all([owner, shopper]); await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
            c = Customer(tenant_id=t.id, full_name="Buyer", phone="+998901230011",
                         status=CustomerStatus.active, telegram_id=shopper.telegram_id)
            s.add(c); await s.flush()
            data = {
                "tenant_id": str(t.id),
                "customer_id": str(c.id),
                "owner": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
                "shopper": {"Authorization": f"Bearer {issue_token(user_id=str(shopper.id))}"},
                "session": Session,
                "telegram": telegram,
            }
            await s.commit()
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _create_contract(client, d, *, price: str = "6000000") -> dict:
    r = await client.post(INSTALLMENTS, headers=d["owner"], json={
        "customer_id": d["customer_id"], "months": 6, "down_payment": "0",
        "items": [{"product_name_snapshot": "Muzlatgich", "qty": 1, "unit_price": price}],
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _set_shop_currency(client, d, code: str) -> None:
    # The settings patch is nested by section; currency lives under `profile`.
    r = await client.patch(SETTINGS, headers=d["owner"], json={"profile": {"currency": code}})
    assert r.status_code == 200, r.text
    assert r.json()["profile"]["currency"] == code


# ── the mandated scenario ───────────────────────────────────────────────────

async def test_switching_the_shop_currency_never_relabels_an_existing_contract(ctx) -> None:
    """The whole defect, start to finish, on every path that shows money."""
    client, d = ctx

    # 1-2. Shop is UZS; the contract is written in UZS.
    old = await _create_contract(client, d)
    assert old["currency"] == "UZS"
    old_id = old["id"]
    old_total = old["total_amount"]

    # 3. The shop switches to USD.
    await _set_shop_currency(client, d, "USD")

    # 4. OLD is still UZS everywhere, and its amounts are untouched.
    detail = (await client.get(f"{INSTALLMENTS}/{old_id}", headers=d["owner"])).json()
    assert detail["currency"] == "UZS"
    assert detail["total_amount"] == old_total          # no conversion

    listed = (await client.get(INSTALLMENTS, headers=d["owner"])).json()["items"]
    assert {row["id"]: row["currency"] for row in listed} == {old_id: "UZS"}

    my_rows = (await client.get("/api/me/installments", headers=d["shopper"])).json()
    assert [row["currency"] for row in my_rows] == ["UZS"]
    my_detail = (await client.get(f"/api/me/installments/{old_id}", headers=d["shopper"])).json()
    assert my_detail["currency"] == "UZS"
    # Same figure on both surfaces (the customer payload keeps 2dp), and
    # crucially the SAME figure as before the switch — no conversion.
    assert Decimal(my_detail["total_amount"]) == Decimal(old_total)

    summary = (await client.get("/api/me/summary", headers=d["shopper"])).json()
    assert summary["next_due_currency"] == "UZS"

    # 5-6. A contract created AFTER the switch is USD, on the same paths.
    new = await _create_contract(client, d, price="1000")
    assert new["currency"] == "USD"
    new_id = new["id"]

    listed = (await client.get(INSTALLMENTS, headers=d["owner"])).json()["items"]
    assert {row["id"]: row["currency"] for row in listed} == {old_id: "UZS", new_id: "USD"}
    assert (await client.get(f"{INSTALLMENTS}/{new_id}", headers=d["owner"])).json()["currency"] == "USD"

    my_rows = (await client.get("/api/me/installments", headers=d["shopper"])).json()
    assert {row["id"]: row["currency"] for row in my_rows} == {old_id: "UZS", new_id: "USD"}

    # 7-8. A payment takes the currency of the contract it lands on.
    pay_old = await client.post(PAYMENTS, headers=d["owner"], json={
        "installment_id": old_id, "amount": "1000000", "method": "cash", "mark_approved": True,
    })
    assert pay_old.status_code in (200, 201), pay_old.text
    assert pay_old.json()["currency"] == "UZS"

    pay_new = await client.post(PAYMENTS, headers=d["owner"], json={
        "installment_id": new_id, "amount": "100", "method": "cash", "mark_approved": True,
    })
    assert pay_new.status_code in (200, 201), pay_new.text
    assert pay_new.json()["currency"] == "USD"

    rows = (await client.get(PAYMENTS, headers=d["owner"])).json()["items"]
    assert {r["installment_id"]: r["currency"] for r in rows} == {old_id: "UZS", new_id: "USD"}

    # 9. Nothing converted: the old contract's money is bit-for-bit what it was.
    assert (await client.get(f"{INSTALLMENTS}/{old_id}", headers=d["owner"])).json()["total_amount"] == old_total

    # 10. Rescheduling restructures the term, never the unit.
    resched = await client.post(f"{INSTALLMENTS}/{old_id}/reschedule", headers=d["owner"],
                                json={"add_months": 3})
    assert resched.status_code == 200, resched.text
    assert resched.json()["currency"] == "UZS"


async def test_the_pdf_context_carries_the_contract_and_payment_currency(ctx) -> None:
    """The document a customer holds is the one that must not be relabelled."""
    from app.services.pdf import _load_installment_context, _load_payment_context

    client, d = ctx
    old = await _create_contract(client, d)
    pay = (await client.post(PAYMENTS, headers=d["owner"], json={
        "installment_id": old["id"], "amount": "1000000", "method": "cash", "mark_approved": True,
    })).json()

    await _set_shop_currency(client, d, "USD")
    new = await _create_contract(client, d, price="1000")
    pay_new = (await client.post(PAYMENTS, headers=d["owner"], json={
        "installment_id": new["id"], "amount": "100", "method": "cash", "mark_approved": True,
    })).json()

    async with d["session"]() as s:
        import uuid
        old_ctx = await _load_installment_context(s, uuid.UUID(old["id"]))
        new_ctx = await _load_installment_context(s, uuid.UUID(new["id"]))
        old_receipt = await _load_payment_context(s, uuid.UUID(pay["id"]))
        new_receipt = await _load_payment_context(s, uuid.UUID(pay_new["id"]))

    assert old_ctx["settings_block"]["currency"] == "UZS"
    assert new_ctx["settings_block"]["currency"] == "USD"
    assert old_receipt["settings_block"]["currency"] == "UZS"
    assert old_receipt["payment"]["currency"] == "UZS"
    assert new_receipt["settings_block"]["currency"] == "USD"
    assert new_receipt["payment"]["currency"] == "USD"


async def test_a_reminder_is_denominated_in_the_contracts_currency(ctx) -> None:
    """The dunning defaults said "so'm" outright — wrong for a USD shop."""
    client, d = ctx
    old = await _create_contract(client, d)
    await _set_shop_currency(client, d, "USD")
    new = await _create_contract(client, d, price="1000")

    d["telegram"].clear()
    assert (await client.post(f"{INSTALLMENTS}/{old['id']}/remind", headers=d["owner"])).status_code == 200
    old_msg = d["telegram"][-1]

    d["telegram"].clear()
    assert (await client.post(f"{INSTALLMENTS}/{new['id']}/remind", headers=d["owner"])).status_code == 200
    new_msg = d["telegram"][-1]

    assert "UZS" in old_msg, old_msg
    assert "USD" in new_msg, new_msg
    # The default templates used to say "so'm" in prose, which is simply wrong
    # for a USD shop — and was wrong for the amount, not just the wording.
    assert "so'm" not in old_msg and "so'm" not in new_msg


# ── the database is the backstop ────────────────────────────────────────────

async def test_the_database_refuses_a_payment_in_the_wrong_currency(ctx) -> None:
    """Not merely an application rule: `fk_payments_contract_currency` holds it.

    An app bug — or a hand-written UPDATE — cannot record a USD payment against
    a so'm contract, which would make `paid_amount` a sum of two units.
    """
    from sqlalchemy.exc import IntegrityError

    from app.models import Payment
    from app.models.enums import PaymentMethod, PaymentStatus

    client, d = ctx
    old = await _create_contract(client, d)

    import uuid
    async with d["session"]() as s:
        s.add(Payment(
            tenant_id=uuid.UUID(d["tenant_id"]),
            installment_id=uuid.UUID(old["id"]),
            customer_id=uuid.UUID(d["customer_id"]),
            currency="USD",                       # the contract is UZS
            amount=Decimal("100"), method=PaymentMethod.cash,
            status=PaymentStatus.pending,
        ))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_the_database_refuses_an_unsupported_currency(ctx) -> None:
    from sqlalchemy.exc import IntegrityError

    from app.models import Installment
    from app.models.enums import InstallmentStatus

    _client, d = ctx
    import uuid
    from datetime import date
    async with d["session"]() as s:
        s.add(Installment(
            tenant_id=uuid.UUID(d["tenant_id"]),
            customer_id=uuid.UUID(d["customer_id"]),
            currency="EUR",
            status=InstallmentStatus.active,
            total_amount=Decimal("100"), down_payment=Decimal("0"),
            principal=Decimal("100"), monthly_amount=Decimal("100"), months=1,
            paid_count=0, paid_amount=Decimal("0"), start_date=date.today(),
        ))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_a_contract_cannot_be_created_without_a_currency(ctx) -> None:
    """No Python-side default: a creation path that forgets fails at flush.

    A default of "UZS" here would let a new write path silently mint so'm
    contracts for a USD shop — the original defect, re-entering through the
    model layer.
    """
    from sqlalchemy.exc import IntegrityError

    from app.models import Installment
    from app.models.enums import InstallmentStatus

    _client, d = ctx
    import uuid
    from datetime import date
    async with d["session"]() as s:
        s.add(Installment(
            tenant_id=uuid.UUID(d["tenant_id"]),
            customer_id=uuid.UUID(d["customer_id"]),
            status=InstallmentStatus.active,
            total_amount=Decimal("100"), down_payment=Decimal("0"),
            principal=Decimal("100"), monthly_amount=Decimal("100"), months=1,
            paid_count=0, paid_amount=Decimal("0"), start_date=date.today(),
        ))
        with pytest.raises(IntegrityError):
            await s.commit()


# ── the resolver itself ─────────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope=None)
def test_a_new_contracts_currency_comes_from_settings_and_falls_back_once() -> None:
    """The ONE place a tenant setting may decide a currency — at creation."""
    from types import SimpleNamespace

    from app.core.currency import DEFAULT_CURRENCY, currency_for_new_contract

    assert currency_for_new_contract(SimpleNamespace(currency="USD")) == "USD"
    assert currency_for_new_contract(SimpleNamespace(currency=" usd ")) == "USD"
    assert currency_for_new_contract(SimpleNamespace(currency="UZS")) == "UZS"
    # No settings row, a blank value, or a currency we cannot hold → the
    # documented default. Never a silent pass-through of junk.
    assert currency_for_new_contract(None) == DEFAULT_CURRENCY
    assert currency_for_new_contract(SimpleNamespace(currency="")) == DEFAULT_CURRENCY
    assert currency_for_new_contract(SimpleNamespace(currency="EUR")) == DEFAULT_CURRENCY


def test_the_allowlist_is_shared_by_the_models_and_the_migration() -> None:
    """Three copies of "which currencies exist" would drift; there is one."""
    import importlib.util

    from app.core.currency import SUPPORTED_CURRENCIES
    from app.models.installment import _CURRENCY_TUPLE_SQL
    from app.models.payment import _CURRENCY_TUPLE_SQL as _PAY_SQL
    spec = importlib.util.spec_from_file_location(
        "_ra0", "alembic/versions/ra0_contract_payment_currency.py")
    ra0 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ra0)

    assert tuple(ra0.SUPPORTED) == tuple(SUPPORTED_CURRENCIES)
    assert _CURRENCY_TUPLE_SQL == _PAY_SQL
    for code in SUPPORTED_CURRENCIES:
        assert f"'{code}'" in _CURRENCY_TUPLE_SQL
