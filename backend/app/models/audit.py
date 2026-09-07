"""Audit trail — every mutating action that's worth investigating."""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .base import TimestampMixin, uuid_pk

if TYPE_CHECKING:
    pass


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_log"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), index=True,
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True,
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(40), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before_json: Mapped[dict | None] = mapped_column("before", JSONB)
    after_json: Mapped[dict | None] = mapped_column("after", JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
