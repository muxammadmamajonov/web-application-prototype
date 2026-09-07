"""Inventory ledger helpers (Round 28).

``record_movement`` is the single write path for stock changes: it appends a
``StockMovement`` row *and* updates the ``Product.stock`` running cache in the
same unit of work. Never mutate ``Product.stock`` directly — go through here so
the ledger and the cache never diverge.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product, StockMovement
from app.models.enums import StockMovementKind


async def record_movement(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    product_id: UUID,
    qty: int,
    kind: StockMovementKind,
    reason: str | None = None,
    by_user_id: UUID | None = None,
    related_installment_id: UUID | None = None,
    product: Product | None = None,
) -> StockMovement:
    """Append a signed movement and adjust ``Product.stock``.

    Does NOT commit — the caller owns the transaction so a sale + its stock
    movement land atomically.

    ``product`` lets a caller that already holds the row for this
    ``(tenant_id, product_id)`` hand it over instead of paying one SELECT per
    movement — a sale with N line items otherwise re-reads N products the caller
    just fetched in a single batch. It is checked against both ids and ignored
    if it doesn't match, so the tenant scope is the same on either path.
    """
    mv = StockMovement(
        tenant_id=tenant_id,
        product_id=product_id,
        qty=qty,
        kind=kind,
        reason=reason,
        by_user_id=by_user_id,
        related_installment_id=related_installment_id,
    )
    db.add(mv)

    if product is not None and (product.id != product_id or product.tenant_id != tenant_id):
        product = None          # mismatched hand-off: fall back to the authoritative read
    if product is None:
        product = (await db.execute(
            select(Product).where(Product.id == product_id, Product.tenant_id == tenant_id)
        )).scalar_one_or_none()
    if product is not None:
        product.stock = (product.stock or 0) + qty

    return mv


async def low_stock_products(db: AsyncSession, tenant_id: UUID) -> list[Product]:
    """Active products whose stock is at/below their ``min_stock_level``."""
    return list((await db.execute(
        select(Product).where(
            Product.tenant_id == tenant_id,
            Product.is_active.is_(True),
            Product.min_stock_level.is_not(None),
            and_(
                Product.stock.is_not(None),
                Product.stock <= Product.min_stock_level,
            ),
        ).order_by(Product.stock.asc())
    )).scalars().all())
