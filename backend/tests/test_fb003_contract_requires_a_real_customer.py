"""FB-003 — a contract is issued against a person, not against a search box.

The client half (the wizard's picker) is pinned in
`frontend/src/routes/tenant/customerSelection.test.tsx`. This is the half that
holds when the request does NOT come from that screen: a replayed request, a
script, a stale tab, another tenant's session.

`customer_id` must be a concrete id belonging to the CALLER's tenant and
belonging to somebody the shop may actually sell to. Missing, malformed,
unknown, cross-tenant, pending and blocked are each refused, and refused
identically for the cross-tenant case — a 404 that admits "this id exists,
just not for you" is itself a leak.
"""
from __future__ import annotations

import os
from uuid import uuid4

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
    """Two shops. Shop A's owner is the caller; every other id is a trap."""
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
            ta = Tenant(slug="shop-a", name="Shop A")
            tb = Tenant(slug="shop-b", name="Shop B")
            s.add_all([ta, tb]); await s.flush()
            s.add_all([TenantSettings(tenant_id=ta.id, currency="UZS"),
                       TenantSettings(tenant_id=tb.id, currency="UZS")])
            owner = User(telegram_id=550001)
            s.add(owner); await s.flush()
            s.add(TenantUser(tenant_id=ta.id, user_id=owner.id, role=TenantRole.owner))
            mine = Customer(tenant_id=ta.id, full_name="Mine", phone="+998901230101",
                            status=CustomerStatus.active)
            theirs = Customer(tenant_id=tb.id, full_name="Theirs", phone="+998901230102",
                              status=CustomerStatus.active)
            pending = Customer(tenant_id=ta.id, full_name="Pending", phone="+998901230103",
                               status=CustomerStatus.pending)
            blocked = Customer(tenant_id=ta.id, full_name="Blocked", phone="+998901230104",
                               status=CustomerStatus.blocked)
            s.add_all([mine, theirs, pending, blocked]); await s.flush()
            data = {
                "auth": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
                "mine": str(mine.id), "theirs": str(theirs.id),
                "pending": str(pending.id), "blocked": str(blocked.id),
            }
            await s.commit()
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _body(customer_id) -> dict:
    return {
        "customer_id": customer_id, "months": 6, "down_payment": "0",
        "items": [{"product_name_snapshot": "Muzlatgich", "qty": 1, "unit_price": "6000000"}],
    }


async def test_a_real_customer_of_this_shop_is_accepted(ctx) -> None:
    """The control: everything below must fail for a reason, not by accident."""
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d["mine"]))
    assert r.status_code in (200, 201), r.text
    assert r.json()["customer_id"] == d["mine"]


async def test_a_missing_customer_id_is_refused(ctx) -> None:
    client, d = ctx
    body = _body(d["mine"]); body.pop("customer_id")
    assert (await client.post(INSTALLMENTS, headers=d["auth"], json=body)).status_code == 422


@pytest.mark.parametrize("bad", ["", "Ali Valiyev", "not-a-uuid", "123", None])
async def test_a_malformed_customer_id_is_refused(ctx, bad) -> None:
    """Including the free-text query itself — the exact FB-003 mistake."""
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"], json=_body(bad))
    assert r.status_code == 422, r.text


async def test_an_unknown_customer_id_is_refused(ctx) -> None:
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"], json=_body(str(uuid4())))
    assert r.status_code == 404
    assert r.json()["detail"] == "customer_not_found"


async def test_another_shops_customer_is_refused_and_indistinguishable(ctx) -> None:
    """Same status and same body as an id that does not exist at all.

    A different answer here would confirm the existence of another tenant's
    customer to anybody willing to enumerate ids.
    """
    client, d = ctx
    theirs = await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d["theirs"]))
    unknown = await client.post(INSTALLMENTS, headers=d["auth"], json=_body(str(uuid4())))
    assert theirs.status_code == unknown.status_code == 404
    assert theirs.json() == unknown.json()


async def test_a_customer_awaiting_approval_cannot_be_sold_to(ctx) -> None:
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d["pending"]))
    assert r.status_code == 409
    assert r.json()["detail"] == "customer_pending_approval"


async def test_a_blocked_customer_cannot_be_sold_to(ctx) -> None:
    client, d = ctx
    r = await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d["blocked"]))
    assert r.status_code == 409
    assert r.json()["detail"] == "customer_blocked"


async def test_a_customer_deleted_after_the_picker_loaded_is_refused(ctx) -> None:
    """The stale-tab case: the id was real when the list rendered."""
    client, d = ctx
    from sqlalchemy import delete

    from app.db import get_db
    from app.main import app
    from app.models import Customer

    # Reach the same session factory the app is using for this test.
    gen = app.dependency_overrides[get_db]()
    session = await gen.__anext__()
    import uuid as _uuid
    await session.execute(delete(Customer).where(Customer.id == _uuid.UUID(d["mine"])))
    await session.commit()
    await gen.aclose()

    r = await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d["mine"]))
    assert r.status_code == 404
    assert r.json()["detail"] == "customer_not_found"
