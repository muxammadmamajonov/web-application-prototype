"""In-app notifications (bell icon in the topbar)."""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .base import TimestampMixin, dt_aware, uuid_pk

if TYPE_CHECKING:
    pass


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[uuid_pk]
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
    )
    # Source installment for reminder notifications — lets the reminder cron
    # dedup on an indexed (tenant_id, kind, installment_id, created_at) key
    # instead of a leading-wildcard LIKE on the body (#75).
    installment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("installments.id", ondelete="SET NULL"),
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str | None] = mapped_column(String(1000))
    link: Mapped[str | None] = mapped_column(String(500))
    read_at: Mapped[dt_aware]
    # Telegram delivery truth. A row can exist for the in-app inbox even when
    # the external send failed; only delivered_at makes a reminder dedupable.
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[dt_aware]
    delivered_at: Mapped[dt_aware]

    __table_args__ = (
        Index(
            "ix_notifications_reminder_dedup",
            "tenant_id", "kind", "installment_id", "created_at",
        ),
        # (MUDD-134) The owner-analytics notification-health aggregate scans by
        # tenant + date window across every kind — the composite above cannot
        # serve that (kind/installment_id sit between tenant_id and created_at).
        Index("ix_notifications_tenant_created", "tenant_id", "created_at"),
    )
