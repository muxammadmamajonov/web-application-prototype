"""Platform-side growth machinery — promo codes, referrals, ad campaigns."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .base import TimestampMixin, dt_aware, money, uuid_pk

if TYPE_CHECKING:
    pass


class PromoCode(Base, TimestampMixin):
    __tablename__ = "promo_codes"

    id: Mapped[uuid_pk]
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # 'percent' | 'fixed'
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    plan_id: Mapped[UUID | None] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"))
    valid_from: Mapped[dt_aware]
    valid_until: Mapped[dt_aware]
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Referral(Base, TimestampMixin):
    __tablename__ = "referrals"

    id: Mapped[uuid_pk]
    referrer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    referee_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"),
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    credit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        # (F-04) At most one Referral per referee tenant, enforced by the DB — not
        # just the app SELECT in maybe_award_referral_conversion. Two concurrent
        # conversions of the SAME referee both pass that unlocked SELECT under READ
        # COMMITTED; this makes the second INSERT raise IntegrityError instead of
        # double-crediting the referrer. Partial (multiple NULLs allowed) because
        # referee_tenant_id is nullable (FK ondelete=SET NULL). Also serves as the
        # lookup index the bare column previously carried via index=True.
        Index(
            "uq_referrals_referee_tenant", "referee_tenant_id",
            unique=True, postgresql_where=text("referee_tenant_id IS NOT NULL"),
        ),
    )


class AdCampaign(Base, TimestampMixin):
    __tablename__ = "ad_campaigns"

    id: Mapped[uuid_pk]
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)   # telegram, instagram, …
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False, index=True)
    budget: Mapped[money]
    spent: Mapped[money]
    leads_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    converted_tenants: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[dt_aware]
    ended_at: Mapped[dt_aware]
    notes: Mapped[str | None] = mapped_column(String(1000))
