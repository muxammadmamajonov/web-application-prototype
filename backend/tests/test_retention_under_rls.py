"""The PII purge deletes through the credential the worker actually holds (MUDD-139).

``tests/test_retention.py`` proves the purge picks the right ROWS. It does not
prove the purge can delete them: it runs on the schema-owner connection, and the
owner is exempt from row-level security.

In production ``purge_expired_pii_job`` runs through ``worker_session`` — the
restricted ``muddato_api`` login, ``SET LOCAL ROLE muddato_worker``, RLS on. If
that role lacks a DELETE policy on any of these tables, the statement removes
nothing, ``_purge_expired`` reports ``0``, the cron log reads clean, and PII
accumulates forever behind a green dashboard. Under RLS a row the policy hid is
indistinguishable from a row that was never there — which is precisely why this
has to be asserted from OUTSIDE the purge's own return value.

So: seed as the owner, purge as the worker, and recount as the owner.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests import _dbcreds

pytestmark = pytest.mark.asyncio(loop_scope="module")

#: Comfortably past every retention window in `scheduler` (the longest is
#: `_AUDIT_RETENTION_DAYS = 365`), so one timestamp expires all five categories.
_EXPIRED_DAYS = 400


@pytest_asyncio.fixture(loop_scope="module")
async def seeded(rls_world):
    """Expired rows in every table ``_purge_expired`` claims to clean."""
    from app.models import AuditLog, Broadcast, Invitation, Notification, SmsMessage

    old = datetime.now(UTC) - timedelta(days=_EXPIRED_DAYS)
    Owner = async_sessionmaker(rls_world.engine, expire_on_commit=False)
    async with Owner() as s:
        s.add_all([
            SmsMessage(tenant_id=rls_world.tenant_a, phone="+998900000101",
                       kind="reminder", text="expired", parts=1, cost_uzs=0,
                       status="sent", created_at=old),
            Notification(user_id=rls_world.customer_user, tenant_id=rls_world.tenant_a,
                         kind="reminder", title="expired", created_at=old),
            Broadcast(tenant_id=rls_world.tenant_a, title="expired", body_uz="x",
                      target="all", status="sent", sent_count=0, failed_count=0,
                      created_at=old),
            AuditLog(tenant_id=rls_world.tenant_a, action="mudd139.expired",
                     created_at=old),
            Invitation(tenant_id=rls_world.tenant_a, kind="customer", preset={},
                       expires_at=old, token_verifier="0" * 64),
        ])
        await s.commit()
    yield rls_world, old, Owner


async def _survivors(Owner, old) -> dict[str, int]:
    """How many of the seeded expired rows are still there, counted as OWNER.

    Counted on the owner connection on purpose: the worker's own view is subject
    to the very policies under test, so "the worker can no longer see it" would
    be satisfied by a purge that deleted nothing.
    """
    from app.models import AuditLog, Broadcast, Invitation, Notification, SmsMessage

    cutoff = old + timedelta(days=1)
    async with Owner() as s:
        async def count(model, where) -> int:
            return (await s.execute(select(func.count(model.id)).where(where))).scalar_one()
        return {
            "sms_messages": await count(SmsMessage, SmsMessage.created_at < cutoff),
            "notifications": await count(Notification, Notification.created_at < cutoff),
            "broadcasts": await count(Broadcast, Broadcast.created_at < cutoff),
            "audit_log": await count(AuditLog, AuditLog.action == "mudd139.expired"),
            "invitations": await count(Invitation, Invitation.expires_at < cutoff),
        }


async def test_the_worker_role_can_actually_delete_expired_pii(seeded, monkeypatch):
    import app.db as db
    from app.config import settings
    from app.services import scheduler

    _world, old, Owner = seeded
    before = await _survivors(Owner, old)
    assert all(v > 0 for v in before.values()), (
        f"fixture seeded nothing to purge — the test would pass vacuously: {before}"
    )

    runtime_url = _dbcreds.runtime_url()
    assert runtime_url, "TEST_RUNTIME_DATABASE_URL / TEST_DATABASE_URL not configured"
    engine = create_async_engine(runtime_url, pool_size=2, max_overflow=0)
    monkeypatch.setattr(settings, "rls_enabled", True)
    db.reset_db_scope()
    try:
        async with db.worker_session(
            factory=async_sessionmaker(engine, expire_on_commit=False)
        ) as s:
            # Prove we really are the restricted role before trusting the result.
            assert (await s.execute(text("select current_user"))).scalar_one() \
                == "muddato_worker"
            reported = await scheduler._purge_expired(s)
    finally:
        await engine.dispose()
        db.reset_db_scope()

    after = await _survivors(Owner, old)
    stuck = {k: v for k, v in after.items() if v}
    assert not stuck, (
        "purge_expired_pii silently deleted nothing under the worker's RLS "
        f"authority for: {stuck} (it reported {reported})"
    )


def test_every_purged_table_is_covered_here() -> None:
    """`_purge_expired` may not grow a category this file does not exercise."""
    import inspect

    from app.services import scheduler

    source = inspect.getsource(scheduler._purge_expired)
    reported = {line.split('"')[1] for line in source.splitlines()
                if line.strip().startswith('counts["')}
    # `risk_signals` expires on its own `expires_at`, not a retention constant,
    # and is covered by tests/test_retention.py.
    covered = {"sms_messages", "notifications", "broadcasts", "audit_log",
               "invitations", "risk_signals"}
    assert reported <= covered, (
        f"_purge_expired gained categories with no RLS proof: {reported - covered}"
    )
