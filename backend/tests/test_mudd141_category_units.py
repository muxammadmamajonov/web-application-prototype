"""MUDD-141 — `installments_by_category` must report UNITS, not just contracts.

THE DEFECT. `GET /tenant/overview`'s category breakdown counted
`count(distinct(Installment.id))` and called it `count` — a contract tally,
not a quantity. A single contract selling 5 phones on one line showed `1`,
same as a contract selling 1. `reports.products()` already got this right
(`sum(InstallmentItem.qty)`, see its docstring); the dashboard was the one
family member left behind.

The fix adds `units_sold` (`sum(qty)`, folded across currency groups — a
dona count is not denominated) and `contract_count` (the same
`count(distinct Installment.id)` as before, renamed), while keeping `count`
as an exact alias of `contract_count` — the 30s overview cache
(`cached_json(f"overview:{tenant.id}")`) has no version segment, so an old
payload can still be served for ~30s after a deploy, and an old client reads
`count` expecting the contract tally it always meant.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

OVERVIEW = "/api/tenant/overview"


@pytest.fixture(autouse=True)
def _no_overview_cache(monkeypatch):
    """Same reasoning as `test_mudd83_installment_category.py`: several tests
    in this file read the overview for the SAME tenant, and the 30s cache
    would make a later test assert against an earlier test's numbers."""
    from app.core import cache

    async def _straight_through(_key, *, ttl, compute, label=None):
        return await compute()

    monkeypatch.setattr(cache, "cached_json", _straight_through)


@pytest_asyncio.fixture
async def ctx():
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
            t = Tenant(slug="units-shop", name="Units Shop")
            s.add(t); await s.flush()
            s.add(TenantSettings(tenant_id=t.id, currency="UZS"))
            owner = User(telegram_id=890001)
            s.add(owner); await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
            c = Customer(tenant_id=t.id, full_name="Buyer", phone="+998901234701",
                         status=CustomerStatus.active)
            s.add(c); await s.flush()

            data = {
                "tenant_id": t.id, "customer_id": c.id, "session": Session,
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


def _installment(d, *, n: int, currency: str, paid: bool = False):
    from app.models import Installment

    total = Decimal("1000000")
    return Installment(
        currency=currency, tenant_id=d["tenant_id"], customer_id=d["customer_id"],
        contract_number=f"{currency}-{n}",
        total_amount=total, down_payment=Decimal("0"), principal=total,
        calc_formula="none", months=1, monthly_amount=total,
        paid_count=1 if paid else 0, paid_amount=total if paid else Decimal("0"),
        status="active", start_date=date.today(), next_due_date=None,
    )


def _item(installment_id, *, category: str, qty: int, unit_price: str):
    from app.models import InstallmentItem

    price = Decimal(unit_price)
    return InstallmentItem(
        installment_id=installment_id, product_name_snapshot="Mahsulot",
        category=category, qty=qty, unit_price=price, line_total=price * qty,
    )


async def test_a_single_line_with_qty_over_one_reports_units_not_one(ctx) -> None:
    """The reported bug, stated as a test: one line, `qty=5`, must show 5
    units sold, not 1 (the contract count)."""
    client, d = ctx
    async with d["session"]() as s:
        ins = _installment(d, n=1, currency="UZS")
        s.add(ins); await s.flush()
        s.add(_item(ins.id, category="smartfonlar", qty=5, unit_price="200000"))
        await s.commit()

    by_category = (await client.get(OVERVIEW, headers=d["auth"])).json()["installments_by_category"]
    assert len(by_category) == 1
    assert by_category[0]["units_sold"] == 5
    assert by_category[0]["contract_count"] == 1
    assert by_category[0]["count"] == 1


async def test_two_lines_of_one_category_on_one_contract_sum_their_qty(ctx) -> None:
    """Two lines, same category, on the SAME contract: units add, the
    contract is still counted once."""
    client, d = ctx
    async with d["session"]() as s:
        ins = _installment(d, n=1, currency="UZS")
        s.add(ins); await s.flush()
        s.add(_item(ins.id, category="smartfonlar", qty=2, unit_price="200000"))
        s.add(_item(ins.id, category="smartfonlar", qty=3, unit_price="200000"))
        await s.commit()

    by_category = (await client.get(OVERVIEW, headers=d["auth"])).json()["installments_by_category"]
    assert len(by_category) == 1
    assert by_category[0]["units_sold"] == 5
    assert by_category[0]["contract_count"] == 1
    assert by_category[0]["count"] == 1


async def test_units_sold_and_contract_count_add_across_currency_groups(ctx) -> None:
    """One UZS contract and one USD contract, same category: `units_sold`
    and `contract_count` are unit-free and must add across the currency
    split; the money stays separated per currency (MUDD-114)."""
    client, d = ctx
    async with d["session"]() as s:
        uzs = _installment(d, n=1, currency="UZS")
        usd = _installment(d, n=1, currency="USD")
        s.add_all([uzs, usd]); await s.flush()
        s.add(_item(uzs.id, category="smartfonlar", qty=4, unit_price="200000"))
        s.add(_item(usd.id, category="smartfonlar", qty=3, unit_price="150"))
        await s.commit()

    by_category = (await client.get(OVERVIEW, headers=d["auth"])).json()["installments_by_category"]
    assert len(by_category) == 1
    row = by_category[0]
    assert row["units_sold"] == 7
    assert row["contract_count"] == 2
    assert row["count"] == 2
    assert row["amount_by_currency"] == {"UZS": 800_000, "USD": 450}
    # Primary currency is UZS (tenant setting) — the scalar legacy field must
    # carry ONLY the UZS side, never a cross-currency sum.
    assert row["amount_uzs"] == 800_000


async def test_count_never_drifts_from_contract_count(ctx) -> None:
    """`count` is a pinned alias — old clients and the 30s overview cache
    still read it as the contract tally."""
    client, d = ctx
    async with d["session"]() as s:
        for n in range(3):
            ins = _installment(d, n=n, currency="UZS")
            s.add(ins); await s.flush()
            s.add(_item(ins.id, category="smartfonlar", qty=n + 1, unit_price="100000"))
        await s.commit()

    by_category = (await client.get(OVERVIEW, headers=d["auth"])).json()["installments_by_category"]
    row = by_category[0]
    assert row["count"] == row["contract_count"] == 3
    assert row["units_sold"] == 1 + 2 + 3
