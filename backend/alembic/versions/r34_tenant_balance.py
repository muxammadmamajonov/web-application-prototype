"""Tenant prepaid balance + ledger (Round 34)

Revision ID: r34tenantbalance
Revises: r33subtzaware
Create Date: 2026-05-30 13:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r34tenantbalance"
down_revision = "r33subtzaware"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("tenants", "balance", server_default=None)

    op.create_table(
        "tenant_balance_txns",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE",
                                name=op.f("fk_tenant_balance_txns_tenant_id_tenants")),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL",
                                name=op.f("fk_tenant_balance_txns_created_by_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_balance_txns")),
    )
    op.create_index(op.f("ix_tenant_balance_txns_tenant_id"), "tenant_balance_txns", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_tenant_balance_txns_tenant_id"), table_name="tenant_balance_txns")
    op.drop_table("tenant_balance_txns")
    op.drop_column("tenants", "balance")
