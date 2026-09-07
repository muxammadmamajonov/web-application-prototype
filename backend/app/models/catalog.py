"""Global product catalog — Category → Brand → Model hierarchy.

Tenants pick from this catalog when creating their own ``Product`` rows,
so that analytics and reporting can roll up across the entire platform
("how many iPhone 15 Pro Max sales last month?") regardless of how each
shop spells the product name.

Variants (colour / storage / RAM) are not their own rows — they're stored
on the ``Product.attributes`` JSON and on ``InstallmentItem.serial_number``
+ ``variant_label`` at sale time. The catalog model defines what fields
exist via ``attributes_template`` (e.g. ``{"color": [...], "storage": [...]}``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

from .base import TimestampMixin, uuid_pk

if TYPE_CHECKING:
    pass


class CatalogCategory(Base, TimestampMixin):
    __tablename__ = "catalog_categories"

    id: Mapped[uuid_pk]
    slug: Mapped[str] = mapped_column(String(48), nullable=False, unique=True, index=True)
    name_uz: Mapped[str] = mapped_column(String(80), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(80), nullable=False)
    icon_key: Mapped[str | None] = mapped_column(String(48))   # legacy lucide icon name
    icon_emoji: Mapped[str | None] = mapped_column(String(16))  # e.g. "📱"
    icon_url: Mapped[str | None] = mapped_column(String(500))   # uploaded category image

    # The category IS the schema. ``attribute_schema`` lists the variant/field
    # axes (color/storage/…) with their type + constraints; ``identifier_schema``
    # lists the per-unit unique IDs (IMEI/VIN) captured by the seller at sale time.
    # See app/core/catalog_schema.py for the JSON shapes.
    attribute_schema: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    identifier_schema: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # Business-type slugs this category belongs to (taxonomy in platform
    # settings). Empty = universal (shown to every segment). Drives the
    # segment-filtered catalog in the tenant product picker.
    business_types: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CatalogBrand(Base, TimestampMixin):
    __tablename__ = "catalog_brands"

    id: Mapped[uuid_pk]
    # Legacy single category (the brand's "primary" category). Kept for the
    # uniqueness constraint + back-compat; ``category_ids`` is the source of
    # truth now that a brand (e.g. Apple, Samsung) spans many categories.
    #
    # (MUDD-135) RESTRICT, not CASCADE. Because ``category_ids`` is the real
    # link, CASCADE deleted a brand out of every category it served whenever
    # the one category that happened to be its primary was removed — deleting
    # `smartfonlar` took Apple and all 10 of its models out of the other six.
    # A category with brands must now be deactivated, not deleted. See
    # `alembic/versions/rl0catintegrity_catalog_referential_integrity.py`.
    category_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("catalog_categories.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    category_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    slug: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    models: Mapped[list[CatalogModel]] = relationship(
        back_populates="brand", cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("category_id", "slug", name="uq_brand_category_slug"),
        # (MUDD-135) One row per brand, full stop. The constraint above is on
        # the legacy primary, so "Apple" could legally exist once per primary
        # category — the pre-``category_ids`` shape, in which one brand's models
        # are split across rows that no query joins back together.
        UniqueConstraint("slug", name="uq_catalog_brands_slug"),
    )


class CatalogModel(Base, TimestampMixin):
    __tablename__ = "catalog_models"

    id: Mapped[uuid_pk]
    brand_id: Mapped[UUID] = mapped_column(
        ForeignKey("catalog_brands.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Which category's field-schema applies to this model. A brand can now span
    # several categories, so the model pins exactly one.
    #
    # (MUDD-135) RESTRICT, not SET NULL. A model with no category resolves no
    # `identifier_schema` and is filtered out of every category-scoped picker —
    # present in the table, gone from the product — and SET NULL produced that
    # state silently on every category delete.
    category_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("catalog_categories.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    release_year: Mapped[int | None] = mapped_column(Integer)
    image_url: Mapped[str | None] = mapped_column(String(500))
    # Per-model selected values keyed by the category axis key, e.g.
    # {"color": ["black","white"], "storage": [128, 256, 512]}.
    attributes_template: Mapped[dict] = mapped_column(JSONB, default=dict)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Tenant-proposed (internal) models. ``tenant_id`` NULL + status "global"
    # = part of the shared catalog everyone sees. A tenant can add a missing
    # model (status "proposed", tenant_id set) that only they use until the
    # platform promotes it to global.
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="global", nullable=False, index=True)
    proposed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    brand: Mapped[CatalogBrand] = relationship(back_populates="models")

    # Includes tenant_id so two shops can each propose the same slug; global
    # rows (tenant_id NULL) are kept unique-per-slug by the platform UI.
    __table_args__ = (
        UniqueConstraint("brand_id", "slug", "tenant_id", name="uq_model_brand_slug_tenant"),
    )


class CatalogColor(Base, TimestampMixin):
    """Global colour taxonomy (uz/ru names + hex). Categories expose a "color"
    field whose options come from this list, so colour is consistent across
    every product."""

    __tablename__ = "catalog_colors"

    id: Mapped[uuid_pk]
    slug: Mapped[str] = mapped_column(String(48), nullable=False, unique=True, index=True)
    name_uz: Mapped[str] = mapped_column(String(48), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(48), nullable=False)
    hex: Mapped[str | None] = mapped_column(String(9))
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
