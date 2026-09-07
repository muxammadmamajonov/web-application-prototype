"""(#857) The subscription credit helper (_credit_by_topup_id, used by the Click webhook)
must never credit a tenant's balance without validating the captured amount against the
requested top-up. It fails closed: no captured amount → no credit. (Payme now validates the
amount at CreateTransaction via its full JSON-RPC protocol, #858.)
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

_AMOUNT = 250_000


async def _make_pending_topup(Session) -> tuple:
    from app.models import Tenant, TenantTopup
    from app.models.enums import TenantStatus

    async with Session() as s:
        t = Tenant(slug="p857-t", name="P857", plan_code="trial",
                   status=TenantStatus.active, balance=Decimal(0))
        s.add(t)
        await s.flush()
        tp = TenantTopup(tenant_id=t.id, amount=Decimal(_AMOUNT), method="payme")
        s.add(tp)
        await s.flush()
        ids = (t.id, tp.id)
        await s.commit()
    return ids


async def _balance_and_status(Session, tid, topup_id) -> tuple:
    from app.models import Tenant, TenantTopup

    async with Session() as s:
        bal = (await s.execute(select(Tenant.balance).where(Tenant.id == tid))).scalar_one()
        st = (await s.execute(
            select(TenantTopup.status).where(TenantTopup.id == topup_id)
        )).scalar_one()
    return int(bal), st


async def test_credit_refused_without_captured_amount(migrated_db) -> None:
    """The headline case: a Payme Perform (no amount) must NOT credit the balance."""
    from app.api.webhooks.subscription import _credit_by_topup_id

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, topup_id = await _make_pending_topup(Session)
    async with Session() as s:
        credited = await _credit_by_topup_id(
            s, str(topup_id), provider_ref="r1", captured_amount=None,
        )
    assert credited is False
    bal, st = await _balance_and_status(Session, tid, topup_id)
    assert bal == 0, f"balance must NOT be credited without a captured amount, got {bal}"
    assert st == "pending", f"top-up must stay un-paid, got {st}"


async def test_credit_rejected_on_amount_mismatch(migrated_db) -> None:
    from app.api.webhooks.subscription import _credit_by_topup_id

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, topup_id = await _make_pending_topup(Session)
    async with Session() as s:
        credited = await _credit_by_topup_id(
            s, str(topup_id), provider_ref="r2", captured_amount=Decimal(_AMOUNT - 1),
        )
    assert credited is False
    bal, st = await _balance_and_status(Session, tid, topup_id)
    assert bal == 0 and st == "pending", f"an amount mismatch must not credit, got bal={bal} st={st}"


async def test_credit_applied_on_matching_amount(migrated_db) -> None:
    from app.api.webhooks.subscription import _credit_by_topup_id

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    tid, topup_id = await _make_pending_topup(Session)
    async with Session() as s:
        credited = await _credit_by_topup_id(
            s, str(topup_id), provider_ref="r3", captured_amount=Decimal(_AMOUNT),
        )
    assert credited is True
    bal, st = await _balance_and_status(Session, tid, topup_id)
    assert bal == _AMOUNT, f"a matching captured amount must credit exactly once, got {bal}"
    assert st == "paid", f"top-up must be marked paid, got {st}"
