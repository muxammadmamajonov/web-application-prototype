"""plans.bot_mode + invitations + tenant_settings bot fields + custom_fields

Revision ID: c3d4plan
Revises: b2c3tenant
Create Date: 2026-05-28 21:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3d4plan"
down_revision = "b2c3tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── plans: bot/customisation flags admin can edit per plan ─────────
    op.add_column("plans", sa.Column(
        "bot_mode", sa.String(16), nullable=False, server_default="shared",
    ))  # "shared" | "tenant"
    op.add_column("plans", sa.Column(
        "allow_custom_domain", sa.Boolean, nullable=False, server_default=sa.false(),
    ))
    op.add_column("plans", sa.Column(
        "max_custom_fields", sa.Integer, nullable=False, server_default="0",
    ))
    op.add_column("plans", sa.Column(
        "max_staff", sa.Integer, nullable=False, server_default="1",
    ))
    op.add_column("plans", sa.Column(
        "broadcasts_per_month", sa.Integer, nullable=False, server_default="0",
    ))

    # Seed reasonable defaults for the existing plans.
    op.execute("UPDATE plans SET bot_mode='shared', max_staff=1,   broadcasts_per_month=0   WHERE code='trial'")
    op.execute("UPDATE plans SET bot_mode='shared', max_staff=3,   broadcasts_per_month=10  WHERE code='lite'")
    op.execute("UPDATE plans SET bot_mode='tenant', max_staff=15,  broadcasts_per_month=200,"
               " max_custom_fields=20 WHERE code='pro'")
    op.execute("UPDATE plans SET bot_mode='tenant', max_staff=999, broadcasts_per_month=9999,"
               " max_custom_fields=999, allow_custom_domain=true WHERE code='custom'")

    # ── tenant_settings: bot config + custom fields + moderation flag ──
    op.add_column("tenant_settings", sa.Column(
        "bot_mode", sa.String(16), nullable=False, server_default="shared",
    ))
    op.add_column("tenant_settings", sa.Column("bot_token_ciphertext", sa.String(500)))
    op.add_column("tenant_settings", sa.Column("bot_username", sa.String(64)))
    op.add_column("tenant_settings", sa.Column(
        "bot_activated_at", sa.DateTime(timezone=True),
    ))
    op.add_column("tenant_settings", sa.Column(
        "customer_form_fields", postgresql.JSONB, nullable=False, server_default="[]",
    ))
    op.add_column("tenant_settings", sa.Column(
        "auto_approve_customers", sa.Boolean, nullable=False, server_default=sa.false(),
    ))
    op.add_column("tenant_settings", sa.Column("custom_domain", sa.String(160)))

    # ── invitations: one-shot tokens for customer / staff onboarding ───
    op.create_table(
        "invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("token", sa.String(40), nullable=False, unique=True, index=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("kind", sa.String(24), nullable=False),  # "customer" | "staff"
        sa.Column("role", sa.String(24)),                  # for staff: owner/manager/sales/accountant/custom
        sa.Column("preset", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("used_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # ── tenant_roles: custom roles per tenant (4 default seeded by app)
    op.create_table(
        "tenant_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("slug", sa.String(48), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("permissions", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("is_builtin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_tenant_role_slug"),
    )


def downgrade() -> None:
    op.drop_table("tenant_roles")
    op.drop_table("invitations")

    for col in [
        "custom_domain", "auto_approve_customers", "customer_form_fields",
        "bot_activated_at", "bot_username", "bot_token_ciphertext", "bot_mode",
    ]:
        op.drop_column("tenant_settings", col)

    for col in [
        "broadcasts_per_month", "max_staff", "max_custom_fields",
        "allow_custom_domain", "bot_mode",
    ]:
        op.drop_column("plans", col)
