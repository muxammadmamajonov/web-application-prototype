"""Customers — end clients of a tenant's shop."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto import EncryptedStr
from app.db import Base

from .base import PHONE_IDENTITY_SQL, TimestampMixin, phone_t, text_short, uuid_pk
from .enums import CustomerStatus, pg_enum

if TYPE_CHECKING:
    from .installment import Installment
    from .tenant import Tenant


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    full_name: Mapped[text_short] = mapped_column(nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    secondary_phone: Mapped[phone_t]
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    # Government-ID fields are Fernet-encrypted at rest via EncryptedStr (#72).
    # The jshshir index is dropped: an index over ciphertext is useless and
    # nothing looks these up by equality (risk scoring hashes jshshir into its
    # own column on customer_risk_signals).
    # Legacy combined field, kept for backwards-compat (auto-derived from series+number)
    passport_id: Mapped[str | None] = mapped_column(EncryptedStr)
    passport_series: Mapped[str | None] = mapped_column(EncryptedStr)    # e.g. "AA"
    passport_number: Mapped[str | None] = mapped_column(EncryptedStr)    # e.g. "1234567"
    # JSHSHIR (Jismoniy Shaxslarning Shaxsiy Identifikasiya Raqami) — Uzbek
    # 14-digit individual taxpayer / national identifier.
    jshshir: Mapped[str | None] = mapped_column(EncryptedStr)
    birthdate: Mapped[date | None] = mapped_column(Date)
    passport_photo_urls: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # ── employment / income (Round 22 — telefon-shop kit) ───────────
    employment_place: Mapped[str | None] = mapped_column(String(200))
    monthly_income:   Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # "Mehnat daftarchasi" (employment record book) scan URLs.
    mehnat_daftarchasi_photo_urls: Mapped[list[str]] = mapped_column(JSONB, default=list)
    address: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[CustomerStatus] = mapped_column(
        pg_enum(CustomerStatus, "customer_status"),
        default=CustomerStatus.active, nullable=False, index=True,
    )
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    tenant: Mapped[Tenant] = relationship(back_populates="customers")
    installments: Mapped[list[Installment]] = relationship(back_populates="customer")

    __table_args__ = (
        # (r96) Functional, not a plain UniqueConstraint over the raw column.
        # Phones are canonicalised to +998XXXXXXXXX on write, but a path that
        # bypasses the Pydantic validator must not be able to enter the same
        # person twice as "+998 90 …" and "998…". Digits are the identity.
        Index(
            "uq_customer_tenant_phone", "tenant_id", text(PHONE_IDENTITY_SQL),
            unique=True, postgresql_where=text("phone IS NOT NULL"),
        ),
        # One Telegram account is at most one customer of one shop. Without this
        # a second claim could bind the same person to a second row in the same
        # tenant, and the two would then accrue separate contracts under one
        # identity. Partial: most customers are merchant-created and unlinked.
        Index(
            "uq_customer_tenant_telegram", "tenant_id", "telegram_id",
            unique=True, postgresql_where=text("telegram_id IS NOT NULL"),
        ),
        # Hot list query: WHERE tenant_id=? ORDER BY created_at DESC.
        Index("ix_customers_tenant_created", "tenant_id", "created_at"),
    )
