"""cross-tenant risk signals + tenant risk_network_opt_in (Round 23)

Revision ID: r23risknet
Revises: r22telefon
Create Date: 2026-05-29 03:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r23risknet"
down_revision = "r22telefon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tenant opt-in flag ────────────────────────────────────────────────
    op.add_column(
        "tenant_settings",
        sa.Column("risk_network_opt_in", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )

    # ── customer_risk_signals ─────────────────────────────────────────────
    # The enum columns below auto-create their pg types as part of the table
    # definition (matching the initial-schema pattern).
    op.create_table(
        "customer_risk_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="SET NULL")),
        sa.Column("source_customer_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("source_installment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("installments.id", ondelete="SET NULL")),
        sa.Column("jshshir_hash", sa.String(64), nullable=False),
        sa.Column("severity", sa.Enum(
            "late_30", "late_60", "late_90", "charged_off", "fraud_reported",
            name="risk_severity"), nullable=False),
        sa.Column("source", sa.Enum(
            "auto", "manual", name="risk_signal_source"),
            nullable=False, server_default="auto"),
        sa.Column("status", sa.Enum(
            "pending_appeal", "published", "withdrawn", name="risk_signal_status"),
            nullable=False, server_default="pending_appeal"),
        sa.Column("appeal_deadline", sa.Date()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("evidence_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_customer_risk_signals_jshshir_hash",
                    "customer_risk_signals", ["jshshir_hash"])
    op.create_index("ix_customer_risk_signals_severity",
                    "customer_risk_signals", ["severity"])
    op.create_index("ix_customer_risk_signals_status",
                    "customer_risk_signals", ["status"])
    op.create_index("ix_customer_risk_signals_source_tenant_id",
                    "customer_risk_signals", ["source_tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_risk_signals_source_tenant_id", table_name="customer_risk_signals")
    op.drop_index("ix_customer_risk_signals_status", table_name="customer_risk_signals")
    op.drop_index("ix_customer_risk_signals_severity", table_name="customer_risk_signals")
    op.drop_index("ix_customer_risk_signals_jshshir_hash", table_name="customer_risk_signals")
    op.drop_table("customer_risk_signals")
    op.execute("DROP TYPE IF EXISTS risk_signal_status")
    op.execute("DROP TYPE IF EXISTS risk_signal_source")
    op.execute("DROP TYPE IF EXISTS risk_severity")
    op.drop_column("tenant_settings", "risk_network_opt_in")
