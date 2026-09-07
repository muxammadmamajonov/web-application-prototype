"""(#740) DELETE /api/auth/logout revokes all outstanding tokens by bumping the user's
``tokens_valid_from`` revocation epoch.

This is the runnable home for the logout-revocation check. The aspirational
``test_p1_wave1_hardening.py`` scaffold also declares a ``test_logout_revokes_all_tokens``,
but that whole module is blanket-skipped (its ``user_token`` / DB-backed-client fixtures
were never wired). This verifies the real #740 behavior end-to-end on Postgres.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


async def test_logout_revokes_all_tokens(migrated_db) -> None:
    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import User

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        u = User(telegram_id=740740)
        s.add(u)
        await s.flush()
        uid = u.id
        # No revocation epoch yet — every outstanding token is currently valid.
        assert u.tokens_valid_from is None
        await s.commit()

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    fresh = issue_token(user_id=str(uid))
    # An already-outstanding token issued an hour before logout, so its iat is strictly
    # below the revocation epoch. The gate is integer-second (iat < tokens_valid_from), so
    # a token minted in the same second as logout would survive; this one definitively won't.
    old = issue_token(
        user_id=str(uid),
        extra={"iat": int((datetime.now(UTC) - timedelta(hours=1)).timestamp())},
    )
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as client:
            # A DELETE /logout with a valid Bearer token bumps the revocation epoch.
            r = await client.delete(
                "/api/auth/logout", headers={"Authorization": f"Bearer {fresh}"},
            )
            assert r.status_code == 200, f"logout should return 200, got {r.status_code}: {r.text[:150]}"
            # The outstanding (pre-logout) token is now rejected — revocation took effect.
            me = await client.get(
                "/api/auth/me", headers={"Authorization": f"Bearer {old}"},
            )
            assert me.status_code == 401, f"a token issued before logout must be revoked, got {me.status_code}"
            assert "revoked" in me.text.lower(), f"expected token_revoked, got {me.text[:150]}"
    finally:
        app.dependency_overrides.pop(get_db, None)

    # The core #740 behavior: logout advanced tokens_valid_from from None to a fresh epoch.
    async with Session() as s:
        tvf = (await s.execute(
            select(User.tokens_valid_from).where(User.id == uid)
        )).scalar_one()
    assert tvf is not None, "logout must set tokens_valid_from (the revocation epoch)"
    assert tvf > datetime.now(UTC) - timedelta(seconds=30), f"tokens_valid_from should be fresh, got {tvf}"
