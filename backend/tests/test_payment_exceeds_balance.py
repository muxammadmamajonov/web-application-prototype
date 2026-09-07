"""H-1 — a payment must never exceed what the contract still owes.

THE DEFECT (2026-08-08 QA pass). On a contract owing 5,000,000 a payment of
50,000,000 was accepted and approved:

    total=5 500 000   paid=50 500 000   status=completed   overpaid_by=45 000 000

Nothing validated the amount against the balance at either creation or
approval. A typo'd extra zero silently books a 10x payment, marks the loan
complete, and corrupts revenue, aging and LTV reporting — with no warning and
no refund trail to unwind it.

THE ROUNDING TRAP. The obvious guard — `amount > principal - paid` — breaks the
LAST legitimate payment of most contracts. `monthly_amount` is quantized
ROUND_HALF_UP, so paying the stated monthly `months` times can land a little
above the principal (10,000,000 over 6 months → 1,666,667 x 6 = 10,000,002).
Rejecting that would block customers from closing their loan while showing them
the amount to pay. The tolerance below is exactly that accumulated drift: under
one currency unit per instalment, so at most `months` units.

These tests pin both sides — the typo is refused, and every legitimate payment,
including the rounded final one and an exact early payoff, still goes through.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

PAY = "/api/tenant/payments"


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
    from app.models import Customer, Installment, Tenant, TenantUser, User
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
            t = Tenant(slug="pay-guard", name="Pay Guard Shop")
            s.add(t); await s.flush()
            owner = User(telegram_id=320001)
            s.add(owner); await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
            c = Customer(tenant_id=t.id, full_name="Payer", phone="+998901110011",
                         status=CustomerStatus.active)
            s.add(c); await s.flush()
            # Owes 5,000,000: principal 5,000,000, nothing paid. Monthly divides
            # evenly so this contract has NO rounding drift of its own.
            clean = Installment(currency="UZS",
                tenant_id=t.id, customer_id=c.id, contract_number="P-CLEAN",
                total_amount=Decimal("6000000"), down_payment=Decimal("1000000"),
                principal=Decimal("5000000"), calc_formula="none", months=5,
                monthly_amount=Decimal("1000000"), paid_count=0,
                paid_amount=Decimal("0"), status="active", start_date=date.today(),
            )
            # 10,000,000 over 6 months → monthly 1,666,667; 6 x monthly is
            # 10,000,002, i.e. 2 units ABOVE principal. The final payment must
            # still be accepted.
            rounded = Installment(currency="UZS",
                tenant_id=t.id, customer_id=c.id, contract_number="P-ROUND",
                total_amount=Decimal("10000000"), down_payment=Decimal("0"),
                principal=Decimal("10000000"), calc_formula="none", months=6,
                monthly_amount=Decimal("1666667"), paid_count=5,
                paid_amount=Decimal("8333335"), status="active", start_date=date.today(),
            )
            s.add_all([clean, rounded]); await s.commit()
            data = {
                "auth": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
                "clean": str(clean.id), "rounded": str(rounded.id),
                "Session": Session,
            }
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _paid(data, iid) -> Decimal:
    from sqlalchemy import select

    from app.models import Installment
    async with data["Session"]() as s:
        return (await s.execute(
            select(Installment.paid_amount).where(Installment.id == iid)
        )).scalar_one()


# ── the defect ───────────────────────────────────────────────────────────────

async def test_a_ten_times_typo_is_refused_at_creation(ctx) -> None:
    """The exact call from the QA pass: 50,000,000 against a 5,000,000 debt."""
    client, data = ctx
    r = await client.post(PAY, headers=data["auth"], json={
        "installment_id": data["clean"], "amount": 50000000, "method": "cash"})
    assert r.status_code == 409, (
        f"a 10x overpayment was accepted with {r.status_code} — this books the "
        "money, closes the loan and corrupts every revenue report"
    )
    assert r.json()["detail"] == "payment_exceeds_balance"
    assert await _paid(data, data["clean"]) == Decimal("0"), "balance was moved anyway"


async def test_the_typo_is_refused_at_approval_too(ctx) -> None:
    """A pending payment may be approved much later, by which time the balance
    can have moved — so the approval path needs the same guard, not just create."""
    client, data = ctx
    r = await client.post(PAY, headers=data["auth"], json={
        "installment_id": data["clean"], "amount": 4000000, "method": "cash"})
    assert r.status_code == 201
    pid = r.json()["id"]
    # Settle the loan by another route, so the pending payment now exceeds it.
    r = await client.post(PAY, headers=data["auth"], json={
        "installment_id": data["clean"], "amount": 5000000,
        "method": "cash", "mark_approved": True})
    assert r.status_code == 201, r.text[:150]

    r = await client.post(f"{PAY}/{pid}/approve", headers=data["auth"])
    assert r.status_code == 409, (
        f"stale pending payment approved onto a settled loan ({r.status_code})")
    assert r.json()["detail"] == "installment_closed"
    assert await _paid(data, data["clean"]) == Decimal("5000000")


async def test_an_absurd_amount_cannot_become_a_stuck_pending_row(ctx) -> None:
    """(QA M-1) An amount at the numeric ceiling used to be accepted at creation
    and then overflow at approval — a 500 on every retry and a payment that
    could never be approved, rejected or cleared. It must be refused up front."""
    client, data = ctx
    r = await client.post(PAY, headers=data["auth"], json={
        "installment_id": data["clean"], "amount": 999999999999, "method": "cash"})
    assert r.status_code in (409, 422), f"got {r.status_code}: {r.text[:150]}"


# ── everything legitimate still works ────────────────────────────────────────

async def test_a_normal_partial_payment_is_untouched(ctx) -> None:
    client, data = ctx
    r = await client.post(PAY, headers=data["auth"], json={
        "installment_id": data["clean"], "amount": 1000000,
        "method": "cash", "mark_approved": True})
    assert r.status_code == 201, r.text[:150]
    assert await _paid(data, data["clean"]) == Decimal("1000000")


async def test_paying_the_balance_exactly_settles_the_loan(ctx) -> None:
    client, data = ctx
    r = await client.post(PAY, headers=data["auth"], json={
        "installment_id": data["clean"], "amount": 5000000,
        "method": "cash", "mark_approved": True})
    assert r.status_code == 201, r.text[:150]
    assert await _paid(data, data["clean"]) == Decimal("5000000")


async def test_the_rounded_final_payment_is_still_accepted(ctx) -> None:
    """The regression this guard could easily cause: the last instalment of a
    contract whose monthly was rounded up lands slightly above the principal.
    Refusing it would trap customers one payment short of closing their loan."""
    client, data = ctx
    r = await client.post(PAY, headers=data["auth"], json={
        "installment_id": data["rounded"], "amount": 1666667,
        "method": "cash", "mark_approved": True})
    assert r.status_code == 201, (
        f"the final rounded instalment was refused ({r.status_code}) — customers "
        f"cannot close their contract: {r.text[:150]}"
    )
    assert await _paid(data, data["rounded"]) == Decimal("10000002")


async def test_the_tolerance_does_not_swallow_a_real_overpayment(ctx) -> None:
    """The rounding allowance must stay tight — it is worth a few units, not a
    licence to overpay."""
    client, data = ctx
    r = await client.post(PAY, headers=data["auth"], json={
        "installment_id": data["rounded"], "amount": 1700000, "method": "cash"})
    assert r.status_code == 409, f"35k over the balance slipped through ({r.status_code})"
