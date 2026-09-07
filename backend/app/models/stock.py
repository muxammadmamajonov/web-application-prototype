"""Stock movements — the inventory ledger (Round 28).

Every change to a product's on-hand quantity is recorded as one immutable
``StockMovement`` row. ``Product.stock`` is the running cache; the ledger is
the source of truth and the audit trail (who, why, when, linked sale).

``qty`` is **signed** — positive adds to stock, negative removes. See
:class:`app.models.enums.StockMovementKind` for the sign convention.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

from .base import TimestampMixin, uuid_pk
from .enums import StockMovementKind, pg_enum

if TYPE_CHECKING:
    from .product import Product


class StockMovement(Base, TimestampMixin):
    __tablename__ = "stock_movements"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    product_id: Mapped[UUID] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)  # signed
    kind: Mapped[StockMovementKind] = mapped_column(
        pg_enum(StockMovementKind, "stock_movement_kind"), nullable=False, index=True,
    )
    reason: Mapped[str | None] = mapped_column(String(500))
    by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    related_installment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("installments.id", ondelete="SET NULL"), index=True,
    )

    product: Mapped[Product] = relationship()
