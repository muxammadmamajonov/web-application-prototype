"""Subscription invoices — billing ledger (Round 27)

Revision ID: r27billing
Revises: r26plans
Create Date: 2026-05-29 07:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r27billing"
down_revision = "r26plans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # New enum for the invoice lifecycle. ``plan_code`` already exists (created
    # by the initial schema for tenants/plans), so we reuse it via
    # create_type=False below.
    invoice_status = postgresql.ENUM(
        "open", "paid", "overdue", "void", name="invoice_status",
    )
    invoice_status.create(op.get_bind(), checkfirst=True)

    plan_code = postgresql.ENUM(
        "trial", "lite", "pro", "custom", name="plan_code", create_type=False,
    )

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plan_code", plan_code, nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("active_installments_count", sa.Integer()),
        sa.Column("unit_price", sa.Numeric(14, 2)),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", postgresql.ENUM(name="invoice_status", create_type=False),
                  nullable=False, server_default="open"),
        sa.Column("line_items", sa.JSON()),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("payment_method", sa.String(20)),
        sa.Column("provider_ref", sa.String(120)),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"])
    op.create_index("ix_invoices_status", "invoices", ["status"])
    op.create_index("ix_invoices_due_at", "invoices", ["due_at"])


def downgrade() -> None:
    op.drop_index("ix_invoices_due_at", table_name="invoices")
    op.drop_index("ix_invoices_status", table_name="invoices")
    op.drop_index("ix_invoices_tenant_id", table_name="invoices")
    op.drop_table("invoices")
    op.execute("DROP TYPE IF EXISTS invoice_status")
