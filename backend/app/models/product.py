"""Catalog products per tenant."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

from .base import TimestampMixin, money, text_short, uuid_pk

if TYPE_CHECKING:
    from .tenant import Tenant


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    sku: Mapped[str | None] = mapped_column(String(64))
    name_uz: Mapped[text_short] = mapped_column(nullable=False)
    name_ru: Mapped[str | None] = mapped_column(String(120))
    description_uz: Mapped[str | None] = mapped_column(String(1500))
    description_ru: Mapped[str | None] = mapped_column(String(1500))
    # Free-text category/brand (legacy — pre-catalog products kept working).
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    brand: Mapped[str | None] = mapped_column(String(64))
    # Reference into the global catalog so analytics can roll up across tenants.
    catalog_model_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("catalog_models.id", ondelete="SET NULL"), index=True,
    )
    base_price: Mapped[money]
    # (M-02) Explicit Numeric(14,2), NOT the bare `Decimal | None` annotation:
    # `Base` declares no `type_annotation_map`, so SQLAlchemy mapped that to an
    # unconstrained NUMERIC (see alembic 7112558dc470:364) while every other money
    # column in the schema is Numeric(14,2). Spelled out rather than reusing the
    # `money` alias because that alias carries `default=Decimal("0")`, and a cost
    # nobody entered is UNKNOWN (NULL), not free.
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    stock: Mapped[int | None] = mapped_column(Integer)
    # Round 28 — when ``stock`` falls to/below this, the product surfaces in
    # the low-stock widget and the daily alert. NULL disables the alert.
    min_stock_level: Mapped[int | None] = mapped_column(Integer)
    photo_urls: Mapped[list[str]] = mapped_column(JSONB, default=list)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)   # colour, RAM, storage…
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tenant: Mapped[Tenant] = relationship(back_populates="products")

    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_product_tenant_sku"),
    )
