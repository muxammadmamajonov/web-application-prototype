"""tenant_settings v2 (biznes profile + calc + reminders) + tenant_staff_invitations

Revision ID: b2c3tenant
Revises: a1c2catalog
Create Date: 2026-05-28 20:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b2c3tenant"
down_revision = "a1c2catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tenant_settings v2: add business profile + calc + reminder fields ──
    op.add_column("tenant_settings", sa.Column("business_name", sa.String(160)))
    op.add_column("tenant_settings", sa.Column("logo_url", sa.String(500)))
    op.add_column("tenant_settings", sa.Column("address", sa.String(500)))
    op.add_column("tenant_settings", sa.Column("contact_phone", sa.String(32)))
    op.add_column("tenant_settings", sa.Column("contact_email", sa.String(160)))
    op.add_column("tenant_settings", sa.Column("contact_telegram", sa.String(64)))

    op.add_column("tenant_settings", sa.Column(
        "reminder_target",  sa.String(16), nullable=False, server_default="owner",
    ))  # "owner" | "channel" | "both"
    op.add_column("tenant_settings", sa.Column("reminder_channel_id", sa.String(32)))
    op.add_column("tenant_settings", sa.Column(
        "reminder_overdue_every_days", sa.Integer, nullable=False, server_default="3",
    ))
    op.add_column("tenant_settings", sa.Column(
        "reminder_templates", postgresql.JSONB, nullable=False, server_default="{}",
    ))

    op.add_column("tenant_settings", sa.Column(
        "default_calc_formula", sa.String(24), nullable=False, server_default="none",
    ))  # "none" | "annuity" | "differentiated" | "markup"
    op.add_column("tenant_settings", sa.Column(
        "default_markup_pct", sa.Numeric(6, 2), nullable=False, server_default="0",
    ))
    op.add_column("tenant_settings", sa.Column(
        "default_max_months", sa.Integer, nullable=False, server_default="12",
    ))
    op.add_column("tenant_settings", sa.Column(
        "default_late_fee_pct_per_day", sa.Numeric(6, 4), nullable=False, server_default="0",
    ))
    op.add_column("tenant_settings", sa.Column(
        "default_min_down_pct", sa.Numeric(6, 2), nullable=False, server_default="0",
    ))

    op.add_column("tenant_settings", sa.Column(
        "payment_methods", postgresql.JSONB, nullable=False,
        server_default='{"cash": true, "card": true, "click": false, "payme": false, "transfer": true}',
    ))
    op.add_column("tenant_settings", sa.Column("click_merchant_id", sa.String(64)))
    op.add_column("tenant_settings", sa.Column("click_service_id", sa.String(64)))
    op.add_column("tenant_settings", sa.Column("click_secret_key", sa.String(255)))
    op.add_column("tenant_settings", sa.Column("payme_merchant_id", sa.String(64)))
    op.add_column("tenant_settings", sa.Column("payme_secret_key", sa.String(255)))

    # ── installments: per-installment calc + late fee overrides ─────────
    op.add_column("installments", sa.Column(
        "calc_formula", sa.String(24), nullable=False, server_default="none",
    ))
    op.add_column("installments", sa.Column(
        "markup_pct", sa.Numeric(6, 2),
    ))
    op.add_column("installments", sa.Column(
        "late_fee_pct_per_day", sa.Numeric(6, 4),
    ))

    # ── tenant_staff_invitations table — track invites separate from
    # tenant_users (which is the activated membership) ──────────────────
    op.create_table(
        "tenant_staff_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("telegram_id", sa.BigInteger, nullable=False, index=True),
        sa.Column("role", sa.String(24), nullable=False, server_default="sales"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        # pending | accepted | rejected | revoked
        sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "telegram_id", name="uq_invite_tenant_tg"),
    )


def downgrade() -> None:
    op.drop_table("tenant_staff_invitations")

    op.drop_column("installments", "late_fee_pct_per_day")
    op.drop_column("installments", "markup_pct")
    op.drop_column("installments", "calc_formula")

    for col in [
        "payme_secret_key", "payme_merchant_id",
        "click_secret_key", "click_service_id", "click_merchant_id",
        "payment_methods",
        "default_min_down_pct", "default_late_fee_pct_per_day",
        "default_max_months", "default_markup_pct", "default_calc_formula",
        "reminder_templates", "reminder_overdue_every_days", "reminder_channel_id", "reminder_target",
        "contact_telegram", "contact_email", "contact_phone",
        "address", "logo_url", "business_name",
    ]:
        op.drop_column("tenant_settings", col)
