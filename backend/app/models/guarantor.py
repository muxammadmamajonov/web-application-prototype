"""Guarantors (kafil) — third parties who back a customer's installment.

A guarantor is a tenant-scoped person record (separate from Customer) that
can be attached to one or more installments via ``installment_guarantors``.
Guarantor data can be entered directly by staff or self-filled by the
guarantor through an invitation (``kind="guarantor"``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto import EncryptedStr
from app.db import Base

from .base import PHONE_IDENTITY_SQL, TimestampMixin, text_short, uuid_pk

if TYPE_CHECKING:
    from .tenant import Tenant


class Guarantor(Base, TimestampMixin):
    __tablename__ = "guarantors"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    full_name: Mapped[text_short] = mapped_column(nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    # Government-ID fields are Fernet-encrypted at rest via EncryptedStr (#72);
    # the jshshir index is dropped (useless over ciphertext, no equality lookups).
    passport_series: Mapped[str | None] = mapped_column(EncryptedStr)
    passport_number: Mapped[str | None] = mapped_column(EncryptedStr)
    jshshir: Mapped[str | None] = mapped_column(EncryptedStr)
    # Relationship to the borrower (e.g. "aka", "ota", "hamkasb").
    relationship_label: Mapped[str | None] = mapped_column("relationship", String(64))
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    address: Mapped[str | None] = mapped_column(String(500))
    photo_urls: Mapped[list[str]] = mapped_column(JSONB, default=list)

    tenant: Mapped[Tenant] = relationship()

    __table_args__ = (
        # (r96) Functional, matching Customer: phones are canonicalised on write,
        # but digits are what makes two records the same person, so the index
        # compares digits rather than the raw column.
        Index(
            "uq_guarantor_tenant_phone", "tenant_id", text(PHONE_IDENTITY_SQL),
            unique=True, postgresql_where=text("phone IS NOT NULL"),
        ),
    )


class InstallmentGuarantor(Base):
    """Join row linking a guarantor to an installment, ordered by ``sequence``."""

    __tablename__ = "installment_guarantors"

    id: Mapped[uuid_pk]
    installment_id: Mapped[UUID] = mapped_column(
        ForeignKey("installments.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    guarantor_id: Mapped[UUID] = mapped_column(
        ForeignKey("guarantors.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "installment_id", "guarantor_id", name="uq_installment_guarantor",
        ),
    )
