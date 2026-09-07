"""Consent / lawful-basis records (#168)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .base import TimestampMixin, uuid_pk


class ConsentRecord(Base, TimestampMixin):
    """One immutable row per consent acceptance — who agreed to what, which version,
    when, and on what lawful basis. Never updated (a withdrawal is a new row), so it's
    a GDPR-style accountability trail for terms acceptance + national-ID processing."""

    __tablename__ = "consent_records"

    id: Mapped[uuid_pk]
    # NULL for platform-level signup consent taken before a tenant exists.
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    subject_kind: Mapped[str] = mapped_column(String(16))     # "user" | "customer"
    subject_id: Mapped[UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(32))             # "terms" | "kyc_processing" | "privacy"
    terms_version: Mapped[str] = mapped_column(String(32))
    lawful_basis: Mapped[str] = mapped_column(String(32))     # "consent" | "contract" | "legal_obligation"
    source: Mapped[str | None] = mapped_column(String(32))    # "signup" | "kyc" | ...
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
