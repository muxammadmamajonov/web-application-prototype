"""SMS fallback + per-customer notification channel preferences (Round 25).

Two tables:
  * ``notification_channels`` — per-customer opt-in record for each delivery
    channel (telegram / sms / email). The reminder cron consults these to
    decide whether SMS fallback is allowed for a given customer.
  * ``sms_messages`` — one row per outbound SMS, used both as an audit log
    and as the source of truth for monthly budget enforcement (sum of
    ``cost_uzs`` for the current month vs ``tenant_settings.sms_budget_uzs_per_month``).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .base import TimestampMixin, dt_aware, uuid_pk
from .enums import NotificationChannelType, SmsStatus, pg_enum

if TYPE_CHECKING:
    pass


class NotificationChannel(Base, TimestampMixin):
    __tablename__ = "notification_channels"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    channel: Mapped[NotificationChannelType] = mapped_column(
        pg_enum(NotificationChannelType, "notification_channel_type"), nullable=False,
    )
    # True for the customer's preferred channel — reminders try this first.
    is_primary: Mapped[bool] = mapped_column(default=False, nullable=False)
    # NULL means the customer hasn't explicitly opted in; the channel row
    # exists only to record a preference. SMS fallback requires opt-in OR
    # the absence of a working Telegram link (see scheduler).
    opted_in_at: Mapped[dt_aware]

    __table_args__ = (
        UniqueConstraint("customer_id", "channel", name="uq_notif_channel_customer_channel"),
    )


class SmsMessage(Base, TimestampMixin):
    __tablename__ = "sms_messages"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True,
    )
    # Source installment + reminder kind, used for same-day dedup so a missed
    # cron + manual catch-up never double-sends.
    installment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("installments.id", ondelete="SET NULL"),
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="manual", index=True)

    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(String(1000), nullable=False)
    parts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    cost_uzs: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    status: Mapped[SmsStatus] = mapped_column(
        pg_enum(SmsStatus, "sms_status"), default=SmsStatus.pending, nullable=False, index=True,
    )
    provider_message_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(String(500))
    # (F-09) Day-bucket for the reminder-cron SMS dedup. NULL for manual / non-cron
    # sends (excluded from the partial unique index below); the reminder cron stamps
    # date.today() so a second overlapping run's INSERT collides on the unique index
    # instead of paying for a duplicate SMS.
    dedup_day: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        # (F-09) Hard idempotency guard for the reminder SMS fallback. The cron's
        # read-then-write "already sent today?" check races under overlapping runs
        # (a lapsed lease — see F-09); this makes the second INSERT fail on the unique
        # violation so we never double-charge a tenant's SMS budget. Partial on two
        # axes: only cron rows (dedup_day NOT NULL) and only non-failed sends
        # participate, so a failed attempt stays retryable the same day.
        Index(
            "uq_sms_reminder_dedup",
            "tenant_id", "installment_id", "kind", "dedup_day",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=sa_text("dedup_day IS NOT NULL AND status IN ('sent', 'pending')"),
        ),
        # (MUDD-134) The owner-analytics notification-health aggregate scans by
        # tenant + date window; nothing else on this table serves that shape.
        Index("ix_sms_messages_tenant_created", "tenant_id", "created_at"),
    )
