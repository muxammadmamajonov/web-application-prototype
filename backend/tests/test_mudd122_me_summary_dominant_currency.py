"""MUDD-122 M5 — `/me/summary`'s headline `currency` must not be chosen by
comparing Decimal amounts across currencies.

THE DEFECT. The original code picked the "dominant" currency with
``max(outstanding_by_currency.items(), key=lambda kv: (kv[1], kv[0]))`` — a
Decimal(UZS) vs Decimal(USD) comparison, the exact cross-currency arithmetic
MUDD-114 banned everywhere else in this module. A customer whose *majority*
of contracts are an already-settled UZS balance, but who still owes real
money on a single USD contract, got told their headline currency was USD —
so a client that has not adopted the per-currency maps (this module's own
history: MUDD-121 found `client.ts` had none) would render nothing in the
unit the majority of the relationship is actually conducted in, and
`isDueToday`-style checks keyed off the scalar fields would look at the
wrong currency's numbers entirely.

The fix picks the dominant currency by CONTRACT COUNT (unit-free, safe to
compare) instead of by outstanding balance. This test pins a case where the
two rules disagree: 3 active UZS contracts (fully paid, hence read as safe)
outnumber 1 active USD contract that still has real, due-today debt.
"""
from __future__ import annotations

import os
from datetime import date
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
            t = Tenant(slug="mixed-currency-shop", name="Mixed Currency Shop")
            s.add(t); await s.flush()
            s.add(TenantSettings(tenant_id=t.id, currency="USD"))
            u = User(telegram_id=440001)
            grant_verified_phone(u)
            s.add(u); await s.flush()

            c = Customer(tenant_id=t.id, full_name="Mixed Customer",
                         phone="+998901114400", status=CustomerStatus.active,
                         telegram_id=u.telegram_id)
            s.add(c); await s.flush()

            def uzs_settled(n: int) -> Installment:
                return Installment(
                    currency="UZS", tenant_id=t.id, customer_id=c.id,
                    contract_number=f"UZS-{n}",
                    total_amount=Decimal("1000000"), down_payment=Decimal("0"),
                    principal=Decimal("1000000"), calc_formula="none", months=1,
                    monthly_amount=Decimal("1000000"), paid_count=1,
                    paid_amount=Decimal("1000000"), status="active",
                    start_date=date.today(), next_due_date=None,
                )

            usd_owing = Installment(
                currency="USD", tenant_id=t.id, customer_id=c.id,
                contract_number="USD-1",
                total_amount=Decimal("500"), down_payment=Decimal("0"),
                principal=Decimal("500"), calc_formula="none", months=1,
                monthly_amount=Decimal("500"), paid_count=0,
                paid_amount=Decimal("0"), status="active",
                start_date=date.today(), next_due_date=date.today(),
            )

            # Three settled UZS contracts (majority by COUNT, zero outstanding)
            # against one USD contract with real, due-today debt (majority by
            # AMOUNT once any currency comparison is attempted).
            s.add_all([uzs_settled(1), uzs_settled(2), uzs_settled(3), usd_owing])
            await s.commit()

            headers = {"Authorization": f"Bearer {issue_token(user_id=str(u.id))}"}
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, headers
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def test_dominant_currency_is_chosen_by_contract_count_not_amount(ctx) -> None:
    """The defect: comparing Decimal(UZS) vs Decimal(USD) picked USD here,
    because the old rule compared amounts across currencies instead of
    contract counts. With 3 UZS contracts vs 1 USD contract, the count-based
    rule must say UZS."""
    client, headers = ctx
    body = (await client.get(SUMMARY, headers=headers)).json()

    assert body["currency"] == "UZS", (
        "dominant currency picked by cross-currency amount comparison instead "
        f"of contract count — got {body['currency']!r}"
    )
    # The majority (UZS) side is genuinely settled — the scalar fields must
    # say so, not leak the USD balance into a UZS-labelled figure.
    assert body["total_outstanding"] == "0"
    assert body["due_today"] == "0"


async def test_the_minority_currencys_real_debt_still_surfaces_in_the_maps(ctx) -> None:
    """A client reading only the scalar fields must not be able to conclude
    'nothing owed anywhere' — the per-currency maps still carry the USD debt
    even though it lost the headline slot."""
    client, headers = ctx
    body = (await client.get(SUMMARY, headers=headers)).json()

    assert "USD" in body["total_outstanding_by_currency"]
    assert Decimal(body["total_outstanding_by_currency"]["USD"]) == Decimal("500")
    assert "USD" in body["due_today_by_currency"]
    assert Decimal(body["due_today_by_currency"]["USD"]) == Decimal("500")
