"""Broadcasts — tenant or platform-wide messages delivered via the bot."""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

from .base import TimestampMixin, dt_aware, uuid_pk
from .enums import BroadcastStatus, BroadcastTarget, pg_enum

if TYPE_CHECKING:
    from .customer import Customer


class Broadcast(Base, TimestampMixin):
    __tablename__ = "broadcasts"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
    )  # NULL → platform-wide broadcast from Muddato HQ
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body_uz: Mapped[str] = mapped_column(String(4000), nullable=False)
    body_ru: Mapped[str | None] = mapped_column(String(4000))
    media_url: Mapped[str | None] = mapped_column(String(500))
    button_label: Mapped[str | None] = mapped_column(String(64))
    button_url: Mapped[str | None] = mapped_column(String(500))

    target: Mapped[BroadcastTarget] = mapped_column(
        pg_enum(BroadcastTarget, "broadcast_target"),
        default=BroadcastTarget.all, nullable=False,
    )
    target_filter: Mapped[dict | None] = mapped_column(JSONB)

    status: Mapped[BroadcastStatus] = mapped_column(
        pg_enum(BroadcastStatus, "broadcast_status"),
        default=BroadcastStatus.draft, nullable=False, index=True,
    )
    scheduled_at: Mapped[dt_aware]
    started_at: Mapped[dt_aware]
    finished_at: Mapped[dt_aware]
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    recipients: Mapped[list[BroadcastRecipient]] = relationship(
        back_populates="broadcast", cascade="all, delete-orphan",
    )


class BroadcastRecipient(Base):
    __tablename__ = "broadcast_recipients"

    id: Mapped[uuid_pk]
    broadcast_id: Mapped[UUID] = mapped_column(
        ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True,
    )
    telegram_id: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    delivered_at: Mapped[dt_aware]
    error: Mapped[str | None] = mapped_column(String(500))

    broadcast: Mapped[Broadcast] = relationship(back_populates="recipients")
    customer: Mapped[Customer | None] = relationship()
