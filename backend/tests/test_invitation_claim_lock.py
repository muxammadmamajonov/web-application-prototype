"""(#180) The one-shot invitation token row is lockable FOR UPDATE, so two
concurrent claims serialize: the second blocks until the first commits used_at,
then sees it and is rejected. Proven by holding the lock in one session and showing
a second FOR UPDATE NOWAIT can't acquire it."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Invitation, Tenant


async def test_invitation_token_row_locks(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as setup:
        t = Tenant(slug="invlockco", name="Inv Lock Co")
        setup.add(t)
        await setup.flush()
        inv = Invitation(
            token="LOCKTESTTK", tenant_id=t.id, kind="customer",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        setup.add(inv)
        await setup.commit()
        inv_id = inv.id

    sa = Session()
    sb = Session()
    try:
        # Session A holds a FOR UPDATE lock on the token row (no commit).
        await sa.execute(select(Invitation).where(Invitation.id == inv_id).with_for_update())
        # Session B can't acquire it (NOWAIT) → concurrent claims serialize.
        with pytest.raises(DBAPIError):
            await sb.execute(
                select(Invitation).where(Invitation.id == inv_id).with_for_update(nowait=True)
            )
    finally:
        await sa.rollback()
        await sa.close()
        await sb.rollback()
        await sb.close()
