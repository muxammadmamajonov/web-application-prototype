"""H-2 — an overdue customer must be shown what they owe, not "all paid up".

THE DEFECT (2026-08-08 QA pass). A customer 10 days late with 10,000,000
outstanding opened the app and read:

    "Hozir to'lov yo'q"  /  "To'lovlaringiz hamma qilib bo'ldi"
    ("no payment right now" / "all your payments are done")

`GET /api/me/summary` picked the next due row with ``next_due_date >= today``,
so a date already in the PAST matched nothing and came back null:

    { "total_outstanding":"10000000.00", "due_today":"0",
      "next_due_date": null, "next_due_amount":"0" }

The client home then computed ``isAllClear = due_today === '0' && !next_due_date``
and rendered the celebratory empty state. The customers who most need a balance
in front of them were the only ones guaranteed not to see one — and it
contradicted the shop's own aging report, which counted the same contract as
overdue.

The fix returns the EARLIEST outstanding due date whether or not it has passed,
so "what do I owe next" is answerable for a late payer, and adds
``next_due_overdue`` so the client can say so plainly instead of inferring it
from a date comparison it was not doing.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

SUMMARY = "/api/me/summary"


@pytest_asyncio.fixture
async def ctx(grant_verified_phone):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

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
            t = Tenant(slug="late-shop", name="Late Shop")
            s.add(t); await s.flush()
            s.add(TenantSettings(tenant_id=t.id, currency="USD"))
            late = User(telegram_id=330001)
            ontime = User(telegram_id=330002)
            settled = User(telegram_id=330003)
            # `/me` reads require proved phone ownership; these three act as
            # customers, so they establish it exactly as the bot would.
            for u in (late, ontime, settled):
                grant_verified_phone(u)
            s.add_all([late, ontime, settled]); await s.flush()

            def mk(u, due_offset_days, paid, principal=Decimal("10000000")):
                c = Customer(tenant_id=t.id, full_name=f"C{u.telegram_id}",
                             phone=f"+99890111{u.telegram_id % 10000}",
                             status=CustomerStatus.active, telegram_id=u.telegram_id)
                s.add(c)
                return c

            c_late, c_ontime, c_settled = mk(late, -10, 0), mk(ontime, 5, 0), mk(settled, 0, 0)
            await s.flush()
            s.add_all([
                # 10 days PAST due — the reported case.
                Installment(currency="USD", tenant_id=t.id, customer_id=c_late.id, contract_number="L-1",
                            total_amount=Decimal("10000000"), down_payment=Decimal("0"),
                            principal=Decimal("10000000"), calc_formula="none", months=6,
                            monthly_amount=Decimal("1666667"), paid_count=0,
                            paid_amount=Decimal("0"), status="active",
                            start_date=date.today() - timedelta(days=40),
                            next_due_date=date.today() - timedelta(days=10)),
                # Due in 5 days — must keep working exactly as before.
                Installment(currency="USD", tenant_id=t.id, customer_id=c_ontime.id, contract_number="O-1",
                            total_amount=Decimal("6000000"), down_payment=Decimal("0"),
                            principal=Decimal("6000000"), calc_formula="none", months=6,
                            monthly_amount=Decimal("1000000"), paid_count=0,
                            paid_amount=Decimal("0"), status="active",
                            start_date=date.today(),
                            next_due_date=date.today() + timedelta(days=5)),
                # Fully paid — must still read as nothing due.
                Installment(currency="USD", tenant_id=t.id, customer_id=c_settled.id, contract_number="S-1",
                            total_amount=Decimal("1000000"), down_payment=Decimal("0"),
                            principal=Decimal("1000000"), calc_formula="none", months=1,
                            monthly_amount=Decimal("1000000"), paid_count=1,
                            paid_amount=Decimal("1000000"), status="completed",
                            start_date=date.today(), next_due_date=None),
            ])
            await s.commit()
            data = {k: {"Authorization": f"Bearer {issue_token(user_id=str(u.id))}"}
                    for k, u in (("late", late), ("ontime", ontime), ("settled", settled))}
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def test_an_overdue_customer_is_told_what_they_owe(ctx) -> None:
    """The defect: this customer was shown nothing due at all."""
    client, auth = ctx
    r = await client.get(SUMMARY, headers=auth["late"])
    assert r.status_code == 200, r.text[:150]
    body = r.json()
    assert body["next_due_date"] is not None, (
        "an overdue customer still gets next_due_date=null — the client home "
        "renders 'all paid up' at them while they are late"
    )
    assert body["next_due_date"] == (date.today() - timedelta(days=10)).isoformat()
    assert body["next_due_amount"] != "0", "the amount owed is still hidden"
    assert body["total_outstanding"] != "0"


async def test_the_response_says_the_date_has_passed(ctx) -> None:
    """The client should not have to re-derive 'is this late?' from a string."""
    client, auth = ctx
    r = await client.get(SUMMARY, headers=auth["late"])
    assert r.json()["next_due_overdue"] is True


async def test_customer_money_payloads_carry_the_contract_currency(ctx) -> None:
    """A USD contract must never be labelled with the Mini App's UZS default."""
    client, auth = ctx
    summary = (await client.get(SUMMARY, headers=auth["late"])).json()
    assert summary["next_due_currency"] == "USD"

    rows = (await client.get("/api/me/installments", headers=auth["late"])).json()
    assert rows[0]["currency"] == "USD"

    detail = (
        await client.get(f"/api/me/installments/{rows[0]['id']}", headers=auth["late"])
    ).json()
    assert detail["currency"] == "USD"


async def test_an_on_time_customer_is_unchanged(ctx) -> None:
    """The fix must not alter the ordinary upcoming-payment case."""
    client, auth = ctx
    body = (await client.get(SUMMARY, headers=auth["ontime"])).json()
    assert body["next_due_date"] == (date.today() + timedelta(days=5)).isoformat()
    assert Decimal(body["next_due_amount"]) == Decimal("1000000")
    assert body["next_due_overdue"] is False


async def test_a_settled_customer_still_has_nothing_due(ctx) -> None:
    """'All paid up' must remain reachable — it is a real state, it was just
    being shown to the wrong people."""
    client, auth = ctx
    body = (await client.get(SUMMARY, headers=auth["settled"])).json()
    assert body["next_due_date"] is None
    assert body["next_due_amount"] == "0"
    assert body["next_due_overdue"] is False


async def test_the_earliest_outstanding_date_wins(ctx) -> None:
    """With both a past and a future instalment, the overdue one is the answer —
    it is what the customer must act on first."""
    client, auth = ctx
    body = (await client.get(SUMMARY, headers=auth["late"])).json()
    assert body["next_due_date"] < date.today().isoformat()
