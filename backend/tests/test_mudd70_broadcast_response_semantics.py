"""H-5 (MUDD-70) — `POST /tenant/broadcasts`'s response is accurate for the
instant it is built, never a final tally, and the endpoint must not report
delivery as inline/synchronous work it has only just scheduled.

THE FULL FINDING (issue MUDD-70, H-5): the dashboard toast on send reads
"{sent_count} ta mijozga yuborildi" ("sent to N customers") using the
CREATE response's `sent_count`, which is always 0 at that instant because
delivery is a `BackgroundTasks` job that has not run yet
(`imports.py`'s own docstring on `create_broadcast` says delivery happens
"after the response returns"). The shop owner reads "sent to 0 customers"
every time, even when the send later succeeds for everyone.

THE FIX FOR THAT is on the frontend
(`frontend/src/routes/tenant/BroadcastsPage.tsx:303`, the toast's copy /
semantics) and is out of this file's scope — that surface is owned by the
parallel "broadcast" lane (MUDD-64) per this issue's file-ownership note, to
avoid two lanes editing the same frontend source concurrently.

What THIS file pins, within backend scope: the response contract the
frontend fix has to work from. `sent_count` / `failed_count` are correct
values for "nothing delivered yet, delivery is scheduled" — 0 and 0 — and
`status` already says "sending", not "sent". A frontend fix has what it
needs to render "yuborish boshlandi" (send started) instead of a false final
count; this guards that contract from regressing (e.g. someone "fixing" the
apparent 0 by computing a pre-count of eligible recipients, which would just
turn the lie into a different, still-wrong number).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


async def test_create_broadcast_response_is_the_pre_delivery_state_not_a_final_tally(
    migrated_db,
) -> None:
    from fastapi import BackgroundTasks

    from app.api.tenant.imports import BroadcastIn, create_broadcast
    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="bx-co", name="BX Co")
        s.add(t)
        await s.flush()
        u = User(telegram_id=990011)
        s.add(u)
        await s.flush()
        s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner, is_active=True))
        await s.commit()
        tenant_id, user_id = t.id, u.id

    scheduled: list[tuple] = []

    class _CapturingBackground(BackgroundTasks):
        def add_task(self, func, *args, **kwargs) -> None:  # type: ignore[override]
            scheduled.append((func, args, kwargs))

    async with Session() as s:
        tenant = await s.get(Tenant, tenant_id)
        membership = (await s.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id, TenantUser.user_id == user_id,
            )
        )).scalar_one()
        payload = BroadcastIn(title="Sale", body_uz="20% off")
        result = await create_broadcast(payload, _CapturingBackground(), (tenant, membership), s)

    # Accurate for "not delivered yet" — this is what the fix has to build on,
    # not what needs fixing on the backend.
    assert result["sent_count"] == 0
    assert result["failed_count"] == 0
    assert result["status"] == "sending"
    # Delivery is DEFERRED, not run inline — the response really does precede
    # any send, so a consumer cannot treat these counts as final.
    assert len(scheduled) == 1, "delivery must be scheduled via BackgroundTasks, not run inline"
    from app.services.notifications import deliver_broadcast_bg
    func, _args, kwargs = scheduled[0]
    assert func is deliver_broadcast_bg
    assert kwargs.get("tenant_id") == tenant_id
