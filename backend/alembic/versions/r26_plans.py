"""Plan capability flags + trial lifecycle columns (Round 26)

Revision ID: r26plans
Revises: r25sms
Create Date: 2026-05-29 06:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r26plans"
down_revision = "r25sms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Plan capability flags ─────────────────────────────────────────────
    op.add_column("plans", sa.Column(
        "allow_pdf_template_upload", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("plans", sa.Column(
        "allow_api_keys", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("plans", sa.Column(
        "allow_outbound_webhooks", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("plans", sa.Column(
        "sms_included_per_month", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("plans", sa.Column(
        "commission_management", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("plans", sa.Column(
        "multi_branch", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("plans", sa.Column(
        "risk_network_access", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("plans", sa.Column(
        "reports_advanced", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("plans", sa.Column("badge", sa.String(20), nullable=True))
    op.add_column("plans", sa.Column(
        "price_per_installment", sa.Numeric(14, 2), nullable=True))

    # ── Tenant trial lifecycle ────────────────────────────────────────────
    op.add_column("tenants", sa.Column(
        "trial_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tenants", sa.Column(
        "suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_tenants_trial_ends_at", "tenants", ["trial_ends_at"])


def downgrade() -> None:
    op.drop_index("ix_tenants_trial_ends_at", table_name="tenants")
    op.drop_column("tenants", "suspended_at")
    op.drop_column("tenants", "trial_ends_at")

    op.drop_column("plans", "price_per_installment")
    op.drop_column("plans", "badge")
    op.drop_column("plans", "reports_advanced")
    op.drop_column("plans", "risk_network_access")
    op.drop_column("plans", "multi_branch")
    op.drop_column("plans", "commission_management")
    op.drop_column("plans", "sms_included_per_month")
    op.drop_column("plans", "allow_outbound_webhooks")
    op.drop_column("plans", "allow_api_keys")
    op.drop_column("plans", "allow_pdf_template_upload")
