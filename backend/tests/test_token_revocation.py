"""(#123) A session token issued before the user's revocation epoch
(``tokens_valid_from``) is rejected at auth — the mechanism behind "log out
everywhere" and a leaked-token reset, without waiting out the 14-day TTL."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException, Response
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import current_user, issue_token
from app.models import User


async def test_token_revocation_epoch(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        u = User(telegram_id=960123, is_active=True)
        s.add(u)
        await s.flush()
        token = issue_token(user_id=str(u.id))

        # No epoch → valid.
        assert (await current_user(response=Response(), token=token, db=s)).id == u.id

        # Epoch in the future (after the token's iat) → revoked.
        u.tokens_valid_from = datetime.now(UTC) + timedelta(hours=1)
        await s.commit()
        with pytest.raises(HTTPException) as ei:
            await current_user(response=Response(), token=token, db=s)
        assert ei.value.status_code == 401

        # Epoch in the past (before the token's iat) → valid again.
        u.tokens_valid_from = datetime.now(UTC) - timedelta(hours=1)
        await s.commit()
        assert (await current_user(response=Response(), token=token, db=s)).id == u.id


# (#740) The epoch enforcement above is the mechanism; these lock the TRIGGER —
# that the logout endpoints actually bump tokens_valid_from. Without this, a
# refactor could stop revoking on logout and the enforcement test would still pass.

async def test_logout_bumps_revocation_epoch(migrated_db) -> None:
    from app.api.auth import logout

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        u = User(telegram_id=960124, is_active=True)
        s.add(u)
        await s.flush()
        assert u.tokens_valid_from is None  # fresh user — no revocation epoch yet
        await logout(user=u, response=Response(), db=s)
        assert isinstance(u.tokens_valid_from, datetime)  # /logout set the epoch


async def test_logout_all_bumps_revocation_epoch(migrated_db) -> None:
    from app.api.auth import logout_all

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        u = User(telegram_id=960125, is_active=True)
        s.add(u)
        await s.flush()
        assert u.tokens_valid_from is None
        await logout_all(user=u, db=s)
        assert isinstance(u.tokens_valid_from, datetime)  # /logout-all set the epoch
