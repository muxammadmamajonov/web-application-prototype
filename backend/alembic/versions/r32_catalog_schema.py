"""Catalog as schema — category attribute/identifier schema, icons, tenant model proposals (Round 32)

Revision ID: r32catalogschema
Revises: r31productformfields
Create Date: 2026-05-30 12:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r32catalogschema"
down_revision = "r31productformfields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── catalog_categories: icon + schema ────────────────────────────────────
    op.add_column("catalog_categories", sa.Column("icon_emoji", sa.String(length=16), nullable=True))
    op.add_column("catalog_categories", sa.Column("icon_url", sa.String(length=500), nullable=True))
    op.add_column(
        "catalog_categories",
        sa.Column("attribute_schema", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "catalog_categories",
        sa.Column("identifier_schema", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("catalog_categories", "attribute_schema", server_default=None)
    op.alter_column("catalog_categories", "identifier_schema", server_default=None)

    # ── catalog_models: tenant proposals ─────────────────────────────────────
    op.add_column("catalog_models", sa.Column("tenant_id", sa.UUID(), nullable=True))
    op.add_column(
        "catalog_models",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="global"),
    )
    op.add_column("catalog_models", sa.Column("proposed_by_user_id", sa.UUID(), nullable=True))
    op.alter_column("catalog_models", "status", server_default=None)

    op.create_index(op.f("ix_catalog_models_tenant_id"), "catalog_models", ["tenant_id"])
    op.create_index(op.f("ix_catalog_models_status"), "catalog_models", ["status"])
    op.create_foreign_key(
        op.f("fk_catalog_models_tenant_id_tenants"), "catalog_models", "tenants",
        ["tenant_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_catalog_models_proposed_by_user_id_users"), "catalog_models", "users",
        ["proposed_by_user_id"], ["id"], ondelete="SET NULL",
    )

    # Widen the model uniqueness to include tenant_id so two shops can each
    # propose the same slug under a brand.
    op.drop_constraint("uq_model_brand_slug", "catalog_models", type_="unique")
    op.create_unique_constraint(
        "uq_model_brand_slug_tenant", "catalog_models", ["brand_id", "slug", "tenant_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_model_brand_slug_tenant", "catalog_models", type_="unique")
    op.create_unique_constraint("uq_model_brand_slug", "catalog_models", ["brand_id", "slug"])

    op.drop_constraint(op.f("fk_catalog_models_proposed_by_user_id_users"), "catalog_models", type_="foreignkey")
    op.drop_constraint(op.f("fk_catalog_models_tenant_id_tenants"), "catalog_models", type_="foreignkey")
    op.drop_index(op.f("ix_catalog_models_status"), table_name="catalog_models")
    op.drop_index(op.f("ix_catalog_models_tenant_id"), table_name="catalog_models")
    op.drop_column("catalog_models", "proposed_by_user_id")
    op.drop_column("catalog_models", "status")
    op.drop_column("catalog_models", "tenant_id")

    op.drop_column("catalog_categories", "identifier_schema")
    op.drop_column("catalog_categories", "attribute_schema")
    op.drop_column("catalog_categories", "icon_url")
    op.drop_column("catalog_categories", "icon_emoji")
