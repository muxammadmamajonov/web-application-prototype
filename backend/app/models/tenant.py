"""Tenant + Plan + Subscription — the SaaS billing root."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

from .base import TimestampMixin, money, slug_t, text_short, uuid_pk
from .enums import SubscriptionStatus, TenantStatus, pg_enum

if TYPE_CHECKING:
    from .customer import Customer
    from .installment import Installment
    from .product import Product
    from .user import TenantUser


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"

    id: Mapped[uuid_pk]
    # Free-form short code (e.g. "lite", "pro", or any owner-chosen slug). Was a
    # native enum until R36; now a plain string so the platform owner can mint
    # up to 5 arbitrary plans.
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name_uz: Mapped[str] = mapped_column(String(80), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(80), nullable=False)
    description_uz: Mapped[str | None] = mapped_column(String(500))
    description_ru: Mapped[str | None] = mapped_column(String(500))
    price_monthly: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    max_active_installments: Mapped[int | None] = mapped_column(Integer)
    features: Mapped[dict] = mapped_column(JSONB, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Bot strategy: "shared" → @MuddatoBot with ?start=t_<slug> deep-links.
    # "tenant" → tenant must paste their own @BotFather-issued token.
    bot_mode: Mapped[str] = mapped_column(String(16), default="shared", nullable=False)
    allow_custom_domain: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Limits: NULL = unlimited (Enterprise). 0 = none allowed.
    max_custom_fields:    Mapped[int | None] = mapped_column(Integer, default=0)
    max_staff:            Mapped[int | None] = mapped_column(Integer, default=1)
    broadcasts_per_month: Mapped[int | None] = mapped_column(Integer, default=0)

    # Round 26 — capability flags (admin can rebalance plans without code change)
    allow_pdf_template_upload: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_api_keys:            Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_outbound_webhooks:   Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sms_included_per_month:    Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    commission_management:     Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    multi_branch:              Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    risk_network_access:       Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reports_advanced:          Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Marketing badge: "limited" | "most_popular" | "full" | "custom" | NULL.
    badge: Mapped[str | None] = mapped_column(String(20))
    # Per-active-installment price for the Custom plan (NULL for fixed plans).
    price_per_installment: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    # ── Round 39: subscription + overage model ───────────────────────────
    max_branches: Mapped[int | None] = mapped_column(Integer, default=1)
    # Per-limit overage config. Shape (each key optional):
    #   {"contract": {"price": int, "enabled": bool, "mode": "soft"|"hard"}, ...}
    # keys: contract | staff | branch | custom_field | broadcast
    # mode "hard" = block when the included limit is reached; "soft" = allow and
    # bill the overage (only when ``enabled``). ``enabled`` false + "hard" = the
    # "not allowed" case.
    overage: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    is_popular:       Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_public:        Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_contact_sales: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[uuid_pk]
    slug: Mapped[slug_t] = mapped_column(unique=True, nullable=False)
    name: Mapped[text_short] = mapped_column(nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        pg_enum(TenantStatus, "tenant_status"),
        default=TenantStatus.demo,
        nullable=False,
        index=True,
    )
    plan_code: Mapped[str] = mapped_column(String(32), default="trial", nullable=False)
    # Per-customer plan overrides (Round 39). Any subset of:
    #   {"price_monthly": int, "limits": {<limit_key>: int|null},
    #    "overage": {<limit_key>: {"price": int, "enabled": bool, "mode": ...}}}
    # Empty = use the plan's defaults.
    plan_overrides: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    billing_email: Mapped[str | None] = mapped_column(String(254))
    #: The number the PLATFORM recorded for this shop at provisioning — the seed
    #: and the fallback, not a second place to maintain the shop's contact
    #: number. What a customer sees is `TenantSettings.contact_phone` once the
    #: owner sets one; both are combined in exactly one place
    #: (`services/tenant_profile.resolve`).
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    # Prepaid subscription balance in so'm. The platform tops it up (after the
    # tenant pays); activating a paid plan deducts the plan price from it.
    # Money: Numeric(14,2) so the ledger is exact and type-consistent with every
    # other money column (installments, plans). Was Integer — mixing int and
    # Decimal money across the schema invites silent truncation/cast bugs (#69).
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)
    locale: Mapped[str] = mapped_column(String(8), default="uz", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tashkent", nullable=False)
    #: DEPRECATED — do not write, do not read directly.
    #:
    #: The shop's logo moved to `TenantSettings.logo_url` when the business
    #: profile became owner-editable, and no writer for this column has existed
    #: since. It stayed readable, so four surfaces went on reading a value
    #: nothing could set: the customer's own screens showed a shop with no logo
    #: forever while its contracts rendered one.
    #:
    #: Kept (rather than dropped) only so any row that still carries a value
    #: does not lose it; `services/tenant_profile.resolve` reads it as a last
    #: resort and is the only thing that may. `test_tenant_profile_source_of_truth`
    #: fails the build if a writer reappears.
    logo_url: Mapped[str | None] = mapped_column(String(500))
    bot_username: Mapped[str | None] = mapped_column(String(64))
    # (#814) The tenant's dedicated forum topic in the Telegram media supergroup,
    # created lazily on first media upload. NULL until provisioned; platform-owner
    # media uses the General topic (id 1) instead.
    telegram_topic_id: Mapped[int | None] = mapped_column(Integer)
    onboarding_state: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Segment + attribution captured by the onboarding wizard. ``business_type``
    # is a taxonomy slug (or "general"/"other"); ``*_other`` holds the free-text
    # when the user picked "other". Used to filter the catalog to the tenant's
    # segment and to analyse the customer base / acquisition channels.
    business_type:       Mapped[str | None] = mapped_column(String(48), index=True)
    business_type_other: Mapped[str | None] = mapped_column(String(120))
    how_found:           Mapped[str | None] = mapped_column(String(48), index=True)
    how_found_other:     Mapped[str | None] = mapped_column(String(120))

    # Round 26 — self-service trial lifecycle. ``trial_ends_at`` is set on
    # signup (now + 14d). When it passes, a daily cron flips status→suspended
    # and stamps ``suspended_at``; 14 days later → blocked. NULL means the
    # tenant isn't on a trial (e.g. admin-provisioned or paying).
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    suspended_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # relationships ----------------------------------------------------------
    memberships: Mapped[list[TenantUser]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan",
    )
    customers: Mapped[list[Customer]] = relationship(back_populates="tenant")
    products: Mapped[list[Product]] = relationship(back_populates="tenant")
    installments: Mapped[list[Installment]] = relationship(back_populates="tenant")
    subscriptions: Mapped[list[TenantSubscription]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Prepaid balance is app-guaranteed non-negative (a plan change refuses
        # on insufficient funds); codify that invariant in the DB too (#69).
        CheckConstraint("balance >= 0", name="balance_nonneg"),
    )


class TenantSubscription(Base, TimestampMixin):
    __tablename__ = "tenant_subscriptions"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        pg_enum(SubscriptionStatus, "subscription_status"),
        default=SubscriptionStatus.trial,
        nullable=False,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount: Mapped[money]
    notes: Mapped[str | None] = mapped_column(String(500))

    tenant: Mapped[Tenant] = relationship(back_populates="subscriptions")
    plan: Mapped[Plan] = relationship()

    __table_args__ = (
        UniqueConstraint("tenant_id", "starts_at", name="uq_tenant_sub_start"),
        # (M-04) At most ONE live subscription per tenant. `uq_tenant_sub_start`
        # only stops two rows sharing a start instant, so two `active` rows with
        # different `starts_at` were legal — and which one billing/entitlement
        # read was then a matter of query order. A PARTIAL unique index is the
        # right shape: `cancelled`, `past_due` and `trial` rows accumulate freely
        # (that history is the billing record), while `active` is unique by
        # construction rather than by every writer remembering to close the old row.
        Index(
            "uq_tenant_sub_one_active", "tenant_id",
            unique=True, postgresql_where=text("status = 'active'"),
        ),
    )


class TenantBalanceTxn(Base):
    """Ledger of every change to a tenant's prepaid balance (so'm)."""

    __tablename__ = "tenant_balance_txns"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)   # signed so'm
    kind: Mapped[str] = mapped_column(String(24), nullable=False)        # topup | plan_charge | adjustment
    note: Mapped[str | None] = mapped_column(String(300))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()",
    )

    __table_args__ = (
        CheckConstraint("balance_after >= 0", name="balance_after_nonneg"),
    )


class TenantTopup(Base, TimestampMixin):
    """A tenant-initiated request to top up its prepaid balance (R36).

    ``method`` is how the tenant intends to pay: ``card`` (manual transfer to a
    platform receiving card, with a ``receipt_url`` photo) or ``payme`` / ``click``
    (online checkout). Status flows ``pending`` → ``paid`` (balance credited) or
    ``rejected``. Online payments flip to ``paid`` via the provider webhook; card
    transfers are approved by a platform admin.
    """

    __tablename__ = "tenant_topups"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)   # so'm
    method: Mapped[str] = mapped_column(String(16), nullable=False)      # card | payme | click
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    receipt_url: Mapped[str | None] = mapped_column(String(500))
    provider_ref: Mapped[str | None] = mapped_column(String(128))       # provider txn id
    note: Mapped[str | None] = mapped_column(String(300))
    # Which platform receiving card a manual `card` transfer was sent to, so the
    # approving admin knows the destination (id + a denormalised label snapshot).
    dest_card_id: Mapped[str | None] = mapped_column(String(64))
    dest_card_label: Mapped[str | None] = mapped_column(String(160))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # (#858) Payme JSON-RPC subscription transaction state (state / create-perform-
    # cancel times / was_completed / reason). The tx id lives in provider_ref above;
    # this mirrors payments.provider_payload for the per-tenant flow.
    provider_payload: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        # (#116) A provider transaction credits a top-up at most once — a duplicate
        # delivery with the same provider_ref fails as an idempotent IntegrityError.
        Index(
            "uq_tenant_topups_provider_ref", "provider_ref",
            unique=True, postgresql_where=text("provider_ref IS NOT NULL"),
        ),
    )
