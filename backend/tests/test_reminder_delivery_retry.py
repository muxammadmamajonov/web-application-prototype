"""Failed Telegram reminders remain retryable and delivery state is truthful."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


async def test_failed_reminder_retries_same_row_until_delivered(migrated_db, monkeypatch) -> None:
    from app.models import Notification, Tenant, User
    from app.services import notifications

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        tenant = Tenant(slug="reminder-retry", name="Reminder Retry")
        user = User(telegram_id=771199)
        s.add_all([tenant, user])
        await s.commit()
        tenant_id, user_id = tenant.id, user.id

    outcomes = iter([False, True])

    async def _telegram(*args, **kwargs) -> bool:
        return next(outcomes)

    monkeypatch.setattr(notifications, "send_to_telegram", _telegram)
    kwargs = dict(
        user_id=user_id, tenant_id=tenant_id, kind="reminder.due_today",
        title="To'lov eslatmasi", body="Bugun to'lov kuni",
        retry_existing=True,
    )
    async with Session() as s:
        assert await notifications.notify_user(s, **kwargs) is False
        assert await notifications.notify_user(s, **kwargs) is True

    async with Session() as s:
        rows = (await s.execute(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.kind == "reminder.due_today",
            )
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].attempt_count == 2
        assert rows[0].last_attempt_at is not None
        assert rows[0].delivered_at is not None
