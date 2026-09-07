"""#75 — reminder-cron dedup is indexed (not a body LIKE), TenantSettings are
cached per tenant, Telegram flood-control is honoured, and broadcasts deliver
off the request path.

The suite has no live DB yet (#79), so behaviour that needs a session is locked
here structurally: the composite index that makes the dedup fast must exist, the
unindexed leading-wildcard LIKE must not come back, the reminder FK must SET NULL
(history survives installment deletion), and the broadcast endpoint must hand
delivery to a background task instead of sending inline on the request thread.
"""
from __future__ import annotations

import inspect


def test_notification_has_installment_fk_set_null() -> None:
    from app.models import Notification

    col = Notification.__table__.c.installment_id
    assert col.nullable is True
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "installments"
    assert fk.column.name == "id"
    # SET NULL, not CASCADE: deleting an installment must not erase the
    # notification history that referenced it.
    assert fk.ondelete == "SET NULL"


def test_reminder_dedup_index_exists_with_expected_columns() -> None:
    from app.models import Notification

    idx = next(
        (i for i in Notification.__table__.indexes
         if i.name == "ix_notifications_reminder_dedup"),
        None,
    )
    assert idx is not None, "composite dedup index missing"
    assert [c.name for c in idx.columns] == [
        "tenant_id", "kind", "installment_id", "created_at",
    ]


def test_notify_user_accepts_installment_id() -> None:
    from app.services.notifications import notify_user

    assert "installment_id" in inspect.signature(notify_user).parameters


def test_send_to_telegram_honours_flood_wait() -> None:
    from app.services.notifications import send_to_telegram

    assert "TelegramRetryAfter" in inspect.getsource(send_to_telegram)


def test_deliver_broadcast_bg_is_async() -> None:
    from app.services.notifications import deliver_broadcast_bg

    assert inspect.iscoroutinefunction(deliver_broadcast_bg)


def test_cron_dedup_is_indexed_not_body_like() -> None:
    import app.services.scheduler as sched

    src = inspect.getsource(sched)
    # The unindexed leading-wildcard LIKE dedup must not come back.
    assert 'body.like(f"%installment=' not in src
    # New behaviour: indexed dedup on the FK + per-tenant settings cache.
    assert "Notification.installment_id == inst.id" in src
    assert "_settings_for(" in src


def test_create_broadcast_delivers_off_request_path() -> None:
    from app.api.tenant import create_broadcast

    params = inspect.signature(create_broadcast).parameters
    annos = [getattr(p.annotation, "__name__", str(p.annotation)) for p in params.values()]
    assert "BackgroundTasks" in annos, "create_broadcast must take a BackgroundTasks param"

    src = inspect.getsource(create_broadcast)
    assert "deliver_broadcast_bg" in src
    assert "background.add_task" in src
    # No inline per-recipient Telegram sends left on the request thread.
    assert "send_to_telegram" not in src
