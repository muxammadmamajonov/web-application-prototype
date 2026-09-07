"""Users (Telegram-linked) + tenant memberships."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

from .base import PHONE_IDENTITY_SQL, TimestampMixin, phone_identity_sql, phone_t, uuid_pk
from .enums import PlatformRole, TenantRole, pg_enum

if TYPE_CHECKING:
    from .tenant import Tenant


class User(Base, TimestampMixin):
    """
    A human (or bot) that can sign into the dashboard or open the WebApp.
    Always linked to a Telegram account — that's the only auth source.
    """

    __tablename__ = "users"

    id: Mapped[uuid_pk]
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))
    photo_url: Mapped[str | None] = mapped_column(String(500))
    #: What the person typed, or an operator recorded. A business record, not
    #: proof of anything: nobody demonstrated they can answer this number.
    phone: Mapped[phone_t]
    #: A number whose OWNERSHIP was proved — today only by a Telegram contact
    #: whose `contact.user_id` equals the sender. Canonical E.164.
    #:
    #: Separate from `phone` on purpose. Collapsing them would mean a person
    #: could promote their own typed number to "verified" by editing a profile
    #: field, which is precisely the claim this column exists to make impossible.
    verified_phone: Mapped[str | None] = mapped_column(String(32))
    verified_phone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: How ownership was proved: "telegram_contact" today, "telegram_oidc" if the
    #: browser fallback is ever built. Never blank when `verified_phone` is set.
    verified_phone_source: Mapped[str | None] = mapped_column(String(24))
    #: A `PhoneEligibility` code for the verified number — why this account can
    #: or cannot become an active customer. A code, never prose: two locales and
    #: an operator screen have to render it.
    phone_eligibility: Mapped[str | None] = mapped_column(String(24))
    #: The name the PERSON chose for themselves. Authoritative for anything
    #: addressed to them, above their Telegram profile name and never touched by
    #: a merchant's own label for them.
    display_name: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(255))
    birthdate: Mapped[date | None] = mapped_column(Date)
    # "male" | "female" | None
    gender: Mapped[str | None] = mapped_column(String(8))
    locale: Mapped[str] = mapped_column(String(8), default="uz", nullable=False)
    # Short, stable referral code used in the shareable invite deep link
    # (?startapp=ref_<code>). Backfilled for existing users; minted lazily.
    ref_code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)
    # (#196) Who referred this user — captured from ?startapp=ref_<code> at /start.
    # Stamp-then-materialize: the Referral row is created later (at the referee's
    # first paid subscription) when their tenant exists. SET NULL so deleting the
    # referrer never cascade-deletes the referee.
    referred_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    # Global (platform-wide) role. NULL = regular tenant user.
    platform_role: Mapped[PlatformRole | None] = mapped_column(
        pg_enum(PlatformRole, "platform_role"), nullable=True, index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # (#123) Session-revocation epoch. A session token whose ``iat`` predates this
    # is rejected, so "log out everywhere" / a leaked-token reset invalidates every
    # outstanding token at once — no per-token tracking. NULL = never revoked.
    tokens_valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[TenantUser]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="TenantUser.user_id",
    )

    __table_args__ = (
        # (#95) Normalized-unique identity: one person = one email / phone.
        # Partial (NULLs allowed) + functional (case-insensitive email, digits-
        # only phone) so "+998 90 …" and "99890…" can't both register.
        Index(
            "uq_users_email_lower", text("lower(email)"),
            unique=True, postgresql_where=text("email IS NOT NULL"),
        ),
        Index(
            "uq_users_phone_digits", text(PHONE_IDENTITY_SQL),
            unique=True, postgresql_where=text("phone IS NOT NULL"),
        ),
        # One proved number belongs to one person. Two accounts both claiming to
        # own it means one of the proofs is wrong, and the database is the only
        # place that can refuse it under concurrency. Digits, for the same reason
        # the typed-phone index uses digits.
        Index(
            "uq_users_verified_phone_digits", text(phone_identity_sql("verified_phone")),
            unique=True, postgresql_where=text("verified_phone IS NOT NULL"),
        ),
    )


class TenantUser(Base, TimestampMixin):
    """A user's role within a specific tenant. A user can belong to many tenants."""

    __tablename__ = "tenant_users"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    role: Mapped[TenantRole] = mapped_column(
        pg_enum(TenantRole, "tenant_role"), nullable=False, default=TenantRole.sales,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    invited_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # "active"  — accepted invitation (or seeded directly by an owner)
    # "pending" — invited but hasn't claimed the link yet
    # "revoked" — invitation explicitly revoked before claim
    invitation_status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    # Per-staff commission override (% of installment total). NULL → use the
    # tenant-wide ``TenantSettings.commission_rate_pct`` default (Round 24).
    commission_rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    tenant: Mapped[Tenant] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user"),
    )
