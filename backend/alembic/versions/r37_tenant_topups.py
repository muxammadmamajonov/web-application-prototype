"""Tenant-initiated balance top-ups (Round 37)

Backs online subscription checkout + manual card-transfer top-ups: a tenant
asks to add funds to its prepaid balance; the platform (or a provider webhook)
confirms and credits the balance.

Revision ID: r37tenanttopups
Revises: r36plancodefreeform
Create Date: 2026-05-30 17:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r37tenanttopups"
down_revision = "r36plancodefreeform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_topups",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("receipt_url", sa.String(length=500), nullable=True),
        sa.Column("provider_ref", sa.String(length=128), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE",
                                name=op.f("fk_tenant_topups_tenant_id_tenants")),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL",
                                name=op.f("fk_tenant_topups_created_by_user_id_users")),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL",
                                name=op.f("fk_tenant_topups_reviewed_by_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_topups")),
    )
    op.create_index(op.f("ix_tenant_topups_tenant_id"), "tenant_topups", ["tenant_id"])
    op.create_index(op.f("ix_tenant_topups_status"), "tenant_topups", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_tenant_topups_status"), table_name="tenant_topups")
    op.drop_index(op.f("ix_tenant_topups_tenant_id"), table_name="tenant_topups")
    op.drop_table("tenant_topups")
