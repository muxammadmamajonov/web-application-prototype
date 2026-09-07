"""M-3 — the customer must not be offered a payment method that is then refused.

THE DEFECT (2026-08-08 QA pass). `GET /me/installments/{id}/payment-methods`
listed `card` ("Karta") for a shop with card payment enabled, and
`POST /me/payments/initiate` then refused it:

    cash  -> 200          card  -> 422  String should match '^(click|payme|cash)$'
    click -> 409 click_not_configured   payme -> 409 payme_not_configured

`click` and `payme` fail gracefully with an explanatory 409. `card` — one of the
two methods the product actually offers — failed as a raw validation error, so a
customer tapping the option the app had just shown them hit a dead end.

The pattern was exactly inverted against the product's own source of truth:
`payment_methods.OFFERED` is `("cash", "card")` and click/payme are WITHDRAWN,
yet the regex admitted both withdrawn methods and rejected an offered one. It
was a hand-copied literal that drifted when the offering changed, so it is now
derived from that module instead — restoring or withdrawing a method updates
the API by itself.

`card` is an OFFLINE method like `cash`: the customer transfers to the shop's
card and uploads a receipt, so it takes the same no-row-yet branch rather than
an online redirect.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

INITIATE = "/api/me/payments/initiate"


def _card_settings(with_card: bool) -> dict:
    return {"cash": True, "card": True,
            "cards": ([{"id": "c1", "number": "8600111122223333", "holder": "SHOP"}]
                      if with_card else [])}


@pytest_asyncio.fixture
async def ctx(grant_verified_phone):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    from datetime import date

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Customer, Installment, Tenant, TenantSettings, User
    from app.models.enums import CustomerStatus

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
            t = Tenant(slug="card-shop", name="Card Shop")
            t2 = Tenant(slug="nocard-shop", name="No Card Shop")
            s.add_all([t, t2]); await s.flush()
            s.add_all([
                TenantSettings(tenant_id=t.id, business_name="Card Shop",
                               payment_methods=_card_settings(True)),
                TenantSettings(tenant_id=t2.id, business_name="No Card Shop",
                               payment_methods=_card_settings(False)),
            ])
            u = User(telegram_id=350001)
            grant_verified_phone(u)
            s.add(u); await s.flush()
            c1 = Customer(tenant_id=t.id, full_name="Buyer", phone="+998901111222",
                          status=CustomerStatus.active, telegram_id=u.telegram_id)
            c2 = Customer(tenant_id=t2.id, full_name="Buyer2", phone="+998901111333",
                          status=CustomerStatus.active, telegram_id=u.telegram_id)
            s.add_all([c1, c2]); await s.flush()

            def ins(t_, c_, ref):
                return Installment(currency="UZS",
                    tenant_id=t_.id, customer_id=c_.id, contract_number=ref,
                    total_amount=Decimal("5000000"), down_payment=Decimal("0"),
                    principal=Decimal("5000000"), calc_formula="none", months=5,
                    monthly_amount=Decimal("1000000"), paid_count=0,
                    paid_amount=Decimal("0"), status="active", start_date=date.today())
            i1, i2 = ins(t, c1, "C-1"), ins(t2, c2, "C-2")
            s.add_all([i1, i2]); await s.commit()
            data = {"auth": {"Authorization": f"Bearer {issue_token(user_id=str(u.id))}"},
                    "with_card": str(i1.id), "no_card": str(i2.id)}
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def test_every_offered_method_is_accepted(ctx) -> None:
    """The contract that was broken: whatever the app lists, initiate takes."""
    client, data = ctx
    offered = (await client.get(
        f"/api/me/installments/{data['with_card']}/payment-methods",
        headers=data["auth"])).json()["methods"]
    assert {m["method"] for m in offered} >= {"cash", "card"}, offered

    for m in offered:
        r = await client.post(INITIATE, headers=data["auth"], json={
            "installment_id": data["with_card"], "amount": 100000, "method": m["method"]})
        assert r.status_code != 422, (
            f"the app offers {m['method']!r} and then refuses it as invalid input — "
            f"a customer tapping it hits a dead end: {r.text[:120]}"
        )
        assert r.status_code == 200, r.text[:150]


async def test_card_takes_the_offline_path_like_cash(ctx) -> None:
    """Card is an offline transfer + receipt upload, not an online redirect."""
    client, data = ctx
    r = await client.post(INITIATE, headers=data["auth"], json={
        "installment_id": data["with_card"], "amount": 100000, "method": "card"})
    assert r.status_code == 200, r.text[:150]
    body = r.json()
    assert body["method"] == "card"
    assert body["pay_url"] is None and body["deep_link"] is None, (
        "card must not claim an online redirect it cannot honour")


async def test_a_shop_with_no_card_configured_says_so(ctx) -> None:
    """Mirrors click/payme: an explanatory 409, never a raw validation error."""
    client, data = ctx
    r = await client.post(INITIATE, headers=data["auth"], json={
        "installment_id": data["no_card"], "amount": 100000, "method": "card"})
    assert r.status_code == 409, r.text[:150]
    assert "card_not_configured" in r.json()["detail"]


async def test_cash_is_unaffected(ctx) -> None:
    client, data = ctx
    r = await client.post(INITIATE, headers=data["auth"], json={
        "installment_id": data["with_card"], "amount": 100000, "method": "cash"})
    assert r.status_code == 200, r.text[:150]
    assert r.json()["method"] == "cash"


async def test_the_withdrawn_providers_still_answer_with_a_reason(ctx) -> None:
    """click/payme keep their explanatory 409 — the fix must not turn a helpful
    message back into a 422."""
    client, data = ctx
    for m in ("click", "payme"):
        r = await client.post(INITIATE, headers=data["auth"], json={
            "installment_id": data["with_card"], "amount": 100000, "method": m})
        assert r.status_code == 409, f"{m}: {r.status_code} {r.text[:120]}"


async def test_nonsense_methods_are_still_rejected(ctx) -> None:
    client, data = ctx
    for m in ("bitcoin", "", "CASH; DROP TABLE"):
        r = await client.post(INITIATE, headers=data["auth"], json={
            "installment_id": data["with_card"], "amount": 100000, "method": m})
        assert r.status_code == 422, f"{m!r} was accepted"


def test_the_accepted_set_is_derived_not_hand_copied() -> None:
    """The literal drifted once; deriving it means it cannot drift again."""
    from app.api.me import _INITIATE_METHODS
    from app.core import payment_methods as pm

    for m in pm.OFFERED:
        assert m in _INITIATE_METHODS, (
            f"{m} is offered to customers but initiate would refuse it")
