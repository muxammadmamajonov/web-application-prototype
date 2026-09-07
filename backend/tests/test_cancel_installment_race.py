"""(#751) cancel_installment must serialize with payment-webhook processing.

The Payme/Click webhook handlers SELECT ... FOR UPDATE the installment before
mutating it. cancel_installment did a plain SELECT (no lock), so a webhook
crediting/cancelling the contract could interleave with the status flip, and two
concurrent cancels both passed the ``already_cancelled`` guard (TOCTOU) → double
audit + double processing. The fix locks the row (``with_for_update()``).
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


def test_cancel_installment_locks_the_row() -> None:
    """Source guard (mirrors test_inline_approve_locks_installment): the handler
    must SELECT ... FOR UPDATE so it serializes with the webhook handlers."""
    from app.api.tenant import cancel_installment

    src = inspect.getsource(cancel_installment)
    assert "with_for_update()" in src, "cancel must lock the installment row (#751)"


async def _seed(engine):
    from app.models import Customer, Installment, Tenant, TenantUser, User
    from app.models.enums import InstallmentStatus, TenantRole

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="cx-t", name="Cancel T")
        s.add(t)
        await s.flush()
        u = User(telegram_id=771010)
        s.add(u)
        await s.flush()
        tu = TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner, is_active=True)
        c = Customer(tenant_id=t.id, full_name="Cx Payer", phone="+998900000222")
        s.add_all([tu, c])
        await s.flush()
        inst = Installment(currency="UZS",
            tenant_id=t.id, customer_id=c.id, total_amount=Decimal("100000"),
            principal=Decimal("100000"), months=12, monthly_amount=Decimal("8333"),
            start_date=date(2026, 1, 1), status=InstallmentStatus.active,
        )
        s.add(inst)
        await s.flush()
        await s.commit()
        return t, u, tu, str(inst.id)


async def test_cancel_respects_concurrent_completion(migrated_db) -> None:
    """The real #751 race: a webhook COMPLETES the installment while a cancel is in
    flight. With the row lock, cancel blocks at its SELECT, then reads the FRESH
    'completed' status under the lock and 409s — the completion wins. Without the
    lock, cancel reads stale 'active', proceeds, and its commit overwrites the
    completion with 'cancelled'. So this reliably fails on the bug.
    """
    from fastapi import HTTPException

    from app.api.tenant import CancelIn, cancel_installment
    from app.models import Installment
    from app.models.enums import InstallmentStatus

    t, u, tu, inst_id = await _seed(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    # A "webhook" holds the row lock and will mark the contract completed.
    holder = Session()
    held = (await holder.execute(
        select(Installment).where(Installment.id == UUID(inst_id)).with_for_update()
    )).scalar_one()

    async def cancel():
        async with Session() as s:
            return await cancel_installment(
                iid=UUID(inst_id), body=CancelIn(reason="x"), user=u, ctx=(t, tu), db=s,
            )

    task = asyncio.create_task(cancel())
    await asyncio.sleep(0.5)  # let cancel reach its SELECT (blocks under the lock)
    held.status = InstallmentStatus.completed
    await holder.commit()  # webhook commits completion + releases the lock
    await holder.close()

    outcome = (await asyncio.gather(task, return_exceptions=True))[0]
    async with Session() as s:
        final = (await s.get(Installment, inst_id)).status

    # The completion wins: cancel saw it under the lock and 409'd; the contract
    # is NOT silently flipped to cancelled.
    assert isinstance(outcome, HTTPException) and outcome.status_code == 409
    assert final == InstallmentStatus.completed
