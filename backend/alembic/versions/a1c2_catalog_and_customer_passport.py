"""catalog (category/brand/model) + customer passport + installment contract_number

Revision ID: a1c2catalog
Revises: 7112558dc470
Create Date: 2026-05-28 19:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a1c2catalog"
down_revision = "7112558dc470"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── catalog_categories ────────────────────────────────────────────────
    op.create_table(
        "catalog_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("slug", sa.String(48), nullable=False, unique=True),
        sa.Column("name_uz", sa.String(80), nullable=False),
        sa.Column("name_ru", sa.String(80), nullable=False),
        sa.Column("icon_key", sa.String(48)),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_catalog_categories_slug", "catalog_categories", ["slug"])

    # ── catalog_brands ────────────────────────────────────────────────────
    op.create_table(
        "catalog_brands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_categories.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("slug", sa.String(48), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("logo_url", sa.String(500)),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("category_id", "slug", name="uq_brand_category_slug"),
    )

    # ── catalog_models ────────────────────────────────────────────────────
    op.create_table(
        "catalog_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("catalog_brands.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("release_year", sa.Integer),
        sa.Column("image_url", sa.String(500)),
        sa.Column("attributes_template", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("suggested_price", sa.Integer),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("brand_id", "slug", name="uq_model_brand_slug"),
    )

    # ── customer passport + jshshir + birthdate ───────────────────────────
    op.add_column("customers", sa.Column("passport_series", sa.String(8)))
    op.add_column("customers", sa.Column("passport_number", sa.String(16)))
    op.add_column("customers", sa.Column("jshshir", sa.String(14)))
    op.add_column("customers", sa.Column("birthdate", sa.Date))
    op.create_index("ix_customers_jshshir", "customers", ["jshshir"])

    # ── installment contract_number + catalog_model_id on items + variant_label
    op.add_column("installments", sa.Column("contract_number", sa.String(40)))
    op.create_index("ix_installments_contract_number", "installments", ["contract_number"])

    op.add_column("installment_items", sa.Column(
        "catalog_model_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("catalog_models.id", ondelete="SET NULL"),
    ))
    op.add_column("installment_items", sa.Column("variant_label", sa.String(120)))

    # ── product → catalog_model link ──────────────────────────────────────
    op.add_column("products", sa.Column(
        "catalog_model_id", postgresql.UUID(as_uuid=True),
        sa.ForeignKey("catalog_models.id", ondelete="SET NULL"),
    ))
    op.create_index("ix_products_catalog_model_id", "products", ["catalog_model_id"])


def downgrade() -> None:
    op.drop_index("ix_products_catalog_model_id", "products")
    op.drop_column("products", "catalog_model_id")

    op.drop_column("installment_items", "variant_label")
    op.drop_column("installment_items", "catalog_model_id")

    op.drop_index("ix_installments_contract_number", "installments")
    op.drop_column("installments", "contract_number")

    op.drop_index("ix_customers_jshshir", "customers")
    op.drop_column("customers", "birthdate")
    op.drop_column("customers", "jshshir")
    op.drop_column("customers", "passport_number")
    op.drop_column("customers", "passport_series")

    op.drop_table("catalog_models")
    op.drop_table("catalog_brands")
    op.drop_index("ix_catalog_categories_slug", "catalog_categories")
    op.drop_table("catalog_categories")
