"""Business type + acquisition attribution + catalog segment tags (Round 35)

Revision ID: r35businesstype
Revises: r34tenantbalance
Create Date: 2026-05-30 19:30:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r35businesstype"
down_revision = "r34tenantbalance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("business_type", sa.String(length=48), nullable=True))
    op.add_column("tenants", sa.Column("business_type_other", sa.String(length=120), nullable=True))
    op.add_column("tenants", sa.Column("how_found", sa.String(length=48), nullable=True))
    op.add_column("tenants", sa.Column("how_found_other", sa.String(length=120), nullable=True))
    op.create_index(op.f("ix_tenants_business_type"), "tenants", ["business_type"])
    op.create_index(op.f("ix_tenants_how_found"), "tenants", ["how_found"])

    op.add_column(
        "catalog_categories",
        sa.Column("business_types", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("catalog_categories", "business_types", server_default=None)


def downgrade() -> None:
    op.drop_column("catalog_categories", "business_types")
    op.drop_index(op.f("ix_tenants_how_found"), table_name="tenants")
    op.drop_index(op.f("ix_tenants_business_type"), table_name="tenants")
    op.drop_column("tenants", "how_found_other")
    op.drop_column("tenants", "how_found")
    op.drop_column("tenants", "business_type_other")
    op.drop_column("tenants", "business_type")
