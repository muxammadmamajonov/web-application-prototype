"""Per-tenant settings + platform-wide settings."""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedStr
from app.db import Base

from .base import TimestampMixin, uuid_pk


class TenantSettings(Base, TimestampMixin):
    """One row per tenant — wide settings document.

    Subdivided into logical groups (business profile, reminders, calc,
    payments) that the frontend renders as separate sub-pages.
    """

    __tablename__ = "tenant_settings"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    # ── business profile ────────────────────────────────────────────
    business_name:    Mapped[str | None] = mapped_column(String(160))
    logo_url:         Mapped[str | None] = mapped_column(String(500))
    address:          Mapped[str | None] = mapped_column(String(500))
    contact_phone:    Mapped[str | None] = mapped_column(String(32))
    contact_email:    Mapped[str | None] = mapped_column(String(160))
    contact_telegram: Mapped[str | None] = mapped_column(String(64))
    business_hours: Mapped[dict] = mapped_column(
        JSONB, default=lambda: {"start": "09:00", "end": "21:00"},
    )
    currency:       Mapped[str] = mapped_column(String(8), default="UZS", nullable=False)
    default_locale: Mapped[str] = mapped_column(String(8), default="uz", nullable=False)
    receipt_footer: Mapped[str | None] = mapped_column(String(500))

    # ── reminders ──────────────────────────────────────────────────
    # "owner" | "channel" | "both"
    reminder_target: Mapped[str] = mapped_column(String(16), default="owner", nullable=False)
    reminder_channel_id: Mapped[str | None] = mapped_column(String(32))
    reminder_overdue_every_days: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    reminder_offsets_days: Mapped[list[int]] = mapped_column(JSONB, default=lambda: [3, 1, 0])
    # Templates: {before_due, due_today, overdue, owner_overdue_alert}
    reminder_templates: Mapped[dict] = mapped_column(JSONB, default=dict)
    notification_prefs: Mapped[dict] = mapped_column(JSONB, default=dict)

    # ── default installment calc ───────────────────────────────────
    # "none" | "annuity" | "differentiated" | "markup"
    default_calc_formula:         Mapped[str] = mapped_column(String(24), default="none", nullable=False)
    default_markup_pct:           Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"), nullable=False)
    default_max_months:           Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    default_late_fee_pct_per_day: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"), nullable=False)
    default_min_down_pct:         Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"), nullable=False)
    # Round 21.4 — discount on the remaining principal when customer
    # closes the installment early. 0 = no discount; 5 = 5% off remaining.
    early_payoff_discount_pct:    Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)

    # ── payments ───────────────────────────────────────────────────
    payment_methods: Mapped[dict] = mapped_column(
        JSONB,
        default=lambda: {"cash": True, "card": True, "click": False, "payme": False, "transfer": True},
    )
    click_merchant_id: Mapped[str | None] = mapped_column(String(64))
    click_service_id:  Mapped[str | None] = mapped_column(String(64))
    click_secret_key:  Mapped[str | None] = mapped_column(String(512))  # encrypted at rest
    payme_merchant_id: Mapped[str | None] = mapped_column(String(64))
    payme_secret_key:  Mapped[str | None] = mapped_column(String(512))  # encrypted at rest
    # (#84) Blind index (keyed HMAC) of the Payme secret so the webhook finds the
    # tenant in one indexed lookup instead of decrypting every tenant's key.
    payme_secret_bidx: Mapped[str | None] = mapped_column(String(64), index=True)
    card_payment_instructions: Mapped[str | None] = mapped_column(String(2000))

    # ── bot strategy override + tenant's own bot ───────────────────
    # Defaults to plan's bot_mode but tenant can switch (within plan caps).
    bot_mode:             Mapped[str] = mapped_column(String(16), default="shared", nullable=False)
    # Fernet-encrypted at rest via EncryptedStr (#83); String(500) holds the
    # ciphertext of a ~46-char bot token comfortably. Reads return plaintext.
    bot_token_ciphertext: Mapped[str | None] = mapped_column(EncryptedStr(500))
    bot_username:         Mapped[str | None] = mapped_column(String(64))
    from datetime import datetime as _dt
    bot_activated_at:     Mapped[_dt | None] = mapped_column(nullable=True)

    # ── customer-form custom fields + moderation ───────────────────
    # JSON array: [{"key","label","type","required","options"?,"order"}]
    customer_form_fields:   Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    auto_approve_customers: Mapped[bool] = mapped_column(default=False, nullable=False)

    # ── catalog product custom fields (Round 29) ───────────────────
    # Named, typed attributes for products (brand, model, storage,
    # colour, IMEI 1/2...) instead of raw {} JSON. Each entry:
    # {"key","label","type","required","options"?,"max_length"?,"integer"?,"order"}
    # An empty list means "use the seeded defaults" (see DEFAULT_PRODUCT_FIELDS).
    product_form_fields:    Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)

    # Optional custom domain (Pro+ plans only).
    custom_domain:        Mapped[str | None] = mapped_column(String(160))

    # ── cross-tenant risk network (Round 23) ───────────────────────
    # Opt-in. When True the tenant both contributes auto/manual signals
    # AND can read aggregated external signals on a customer's JSHSHIR.
    # Default False — privacy-by-default; the network is reciprocal.
    risk_network_opt_in: Mapped[bool] = mapped_column(default=False, nullable=False)

    # ── staff commission (Round 24 reports) ────────────────────────
    # Tenant-wide default commission rate (% of installment total) used by the
    # staff-performance report. A per-staff override lives on
    # ``TenantUser.commission_rate_pct`` (NULL → fall back to this default).
    commission_rate_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("0"), nullable=False,
    )

    # ── SMS fallback (Round 25, Eskiz.uz) ──────────────────────────
    # Monthly SMS budget in so'm. 0 (default) disables SMS fallback entirely
    # — Lite plans get 0, Pro 100_000, Premium custom. The reminder cron
    # checks month-to-date spend against this before sending.
    sms_budget_uzs_per_month: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False,
    )
    # Pro+ alphanumeric sender ID (e.g. "MUDDATO" or the shop's brand). When
    # NULL the platform default sender (numeric short code) is used. Eskiz
    # requires the alpha-name to be pre-registered/approved on their side.
    sms_sender_alpha: Mapped[str | None] = mapped_column(String(16))

    # ── contract PDF customisation (Round 19, Pro+ plan) ────────────
    # Raw HTML uploaded by the tenant. Rendered via Jinja2 with the same
    # context as the bundled default. Validated at save via
    # `app.services.pdf.validate_custom_template` so a hostile template
    # can't escape into config / request / dunders.
    contract_template_html: Mapped[str | None] = mapped_column(String(200_000))

    extra: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_settings_tenant"),
    )


class PlatformSetting(Base, TimestampMixin):
    """Singleton key/value store for Muddato HQ-wide configuration."""

    __tablename__ = "platform_settings"

    id: Mapped[uuid_pk]
    key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    description: Mapped[str | None] = mapped_column(String(500))
