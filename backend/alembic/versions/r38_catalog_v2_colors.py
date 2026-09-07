"""Catalog v2: multi-category brands, model category link, colors taxonomy (Round 38)

* Brands can now belong to several categories (``category_ids`` JSON). The old
  single ``category_id`` column is kept (nullable) for back-compat / uniqueness
  and backfilled into ``category_ids``.
* Models gain an explicit ``category_id`` (which category's field-schema applies)
  since a brand may now span categories. Backfilled from the brand's category.
* ``catalog_models.suggested_price`` is dropped — prices change too often to be
  worth seeding; the seller enters the price at sale time.
* New ``catalog_colors`` taxonomy (uz/ru names + hex) so "color" is a reusable
  field across products. Seeded with common colors.

Revision ID: r38catalogv2colors
Revises: r37tenanttopups
Create Date: 2026-05-31 09:00:00+00:00
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "r38catalogv2colors"
down_revision = "r37tenanttopups"
branch_labels = None
depends_on = None


_COLORS = [
    # (slug, name_uz, name_ru, hex)
    ("black", "Qora", "Чёрный", "#000000"),
    ("white", "Oq", "Белый", "#FFFFFF"),
    ("gray", "Kulrang", "Серый", "#808080"),
    ("silver", "Kumush", "Серебристый", "#C0C0C0"),
    ("space-gray", "Kosmik kulrang", "Серый космос", "#4A4A4A"),
    ("graphite", "Grafit", "Графитовый", "#383838"),
    ("gold", "Oltin", "Золотой", "#D4AF37"),
    ("rose-gold", "Pushti oltin", "Розовое золото", "#B76E79"),
    ("blue", "Koʻk", "Синий", "#1E3A8A"),
    ("sky-blue", "Moviy", "Голубой", "#38BDF8"),
    ("navy", "Toʻq koʻk", "Тёмно-синий", "#1E293B"),
    ("turquoise", "Turkuaz", "Бирюзовый", "#14B8A6"),
    ("red", "Qizil", "Красный", "#DC2626"),
    ("green", "Yashil", "Зелёный", "#16A34A"),
    ("yellow", "Sariq", "Жёлтый", "#FACC15"),
    ("orange", "Toʻq sariq", "Оранжевый", "#F97316"),
    ("pink", "Pushti", "Розовый", "#EC4899"),
    ("purple", "Binafsha", "Фиолетовый", "#7C3AED"),
    ("brown", "Jigarrang", "Коричневый", "#92400E"),
    ("beige", "Bej", "Бежевый", "#E7D8C9"),
]


def upgrade() -> None:
    # Brands → multi-category
    op.add_column("catalog_brands", sa.Column("category_ids", sa.JSON(), nullable=False, server_default="[]"))
    op.alter_column("catalog_brands", "category_id", existing_type=sa.UUID(), nullable=True)
    op.execute(
        "UPDATE catalog_brands SET category_ids = json_build_array(category_id::text) "
        "WHERE category_id IS NOT NULL"
    )
    op.alter_column("catalog_brands", "category_ids", server_default=None)

    # Models → explicit category link + drop suggested_price
    op.add_column("catalog_models", sa.Column("category_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_catalog_models_category_id_catalog_categories"),
        "catalog_models", "catalog_categories", ["category_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(op.f("ix_catalog_models_category_id"), "catalog_models", ["category_id"])
    op.execute(
        "UPDATE catalog_models m SET category_id = b.category_id "
        "FROM catalog_brands b WHERE m.brand_id = b.id"
    )
    op.drop_column("catalog_models", "suggested_price")

    # Colors taxonomy
    op.create_table(
        "catalog_colors",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("slug", sa.String(length=48), nullable=False),
        sa.Column("name_uz", sa.String(length=48), nullable=False),
        sa.Column("name_ru", sa.String(length=48), nullable=False),
        sa.Column("hex", sa.String(length=9), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_colors")),
        sa.UniqueConstraint("slug", name=op.f("uq_catalog_colors_slug")),
    )
    op.bulk_insert(
        sa.table(
            "catalog_colors",
            sa.column("id", sa.UUID()),
            sa.column("slug", sa.String()),
            sa.column("name_uz", sa.String()),
            sa.column("name_ru", sa.String()),
            sa.column("hex", sa.String()),
            sa.column("sort_order", sa.Integer()),
        ),
        [
            {"id": uuid.uuid4(), "slug": s, "name_uz": uz, "name_ru": ru, "hex": hx, "sort_order": i * 10}
            for i, (s, uz, ru, hx) in enumerate(_COLORS)
        ],
    )


def downgrade() -> None:
    op.drop_table("catalog_colors")
    op.add_column("catalog_models", sa.Column("suggested_price", sa.Integer(), nullable=True))
    op.drop_index(op.f("ix_catalog_models_category_id"), table_name="catalog_models")
    op.drop_constraint(op.f("fk_catalog_models_category_id_catalog_categories"), "catalog_models", type_="foreignkey")
    op.drop_column("catalog_models", "category_id")
    op.alter_column("catalog_brands", "category_id", existing_type=sa.UUID(), nullable=False)
    op.drop_column("catalog_brands", "category_ids")
