"""Owner dashboard analytics response shapes (MUDD-134).

Every monetary field follows the MUDD-114 contract already used by
``app.services.reports``: a ``*_by_currency`` map is the source of truth, and
the bare scalar beside it is only the ``primary_currency`` share — never a
cross-currency sum. See ``app.core.currency`` for why no conversion happens.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CashflowPoint(BaseModel):
    period: str
    count: int
    amount: Decimal
    by_currency: dict[str, Decimal]


class CashflowTrend(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from")
    to: date
    granularity: str
    currency: str
    series: list[CashflowPoint]
    total: Decimal
    total_by_currency: dict[str, Decimal]


class NotificationChannelHealth(BaseModel):
    channel: str
    sent: int
    failed: int
    pending: int


class InventorySummary(BaseModel):
    currency: str
    active_products_count: int
    units_in_stock: int
    value: Decimal
    products_missing_cost: int


class OwnerSummary(BaseModel):
    """``GET /tenant/analytics/summary`` response."""

    model_config = ConfigDict(populate_by_name=True)

    tenant_id: str
    primary_currency: str
    from_: date = Field(alias="from")
    to: date

    revenue_by_currency: dict[str, Decimal]
    revenue: Decimal

    debt_by_currency: dict[str, Decimal]
    debt: Decimal

    overdue_by_currency: dict[str, Decimal]
    overdue: Decimal
    overdue_count: int

    upcoming_by_currency: dict[str, Decimal]
    upcoming: Decimal
    upcoming_count: int

    cashflow: CashflowTrend
    contracts_by_status: dict[str, int]
    customers_count: int
    inventory: InventorySummary
    notifications: list[NotificationChannelHealth]


class CategorySalesRow(BaseModel):
    """One (category, brand, model) bucket. ``*_slug`` is ``None`` for the
    legacy/uncategorized buckets — see ``app.services.analytics.category_sales``."""

    category_slug: str | None
    category_name_uz: str | None
    category_name_ru: str | None
    brand_slug: str | None
    brand_name: str | None
    model_slug: str | None
    model_name: str | None
    qty: int
    revenue: Decimal
    revenue_by_currency: dict[str, Decimal]


class CategoryTotal(BaseModel):
    """One category's rollup — always present for every catalog category, even
    with zero sales, plus a synthetic ``None`` bucket for uncategorized sales."""

    category_slug: str | None
    category_name_uz: str | None
    category_name_ru: str | None
    qty: int
    revenue: Decimal
    revenue_by_currency: dict[str, Decimal]


class CategorySalesReport(BaseModel):
    """``GET /tenant/analytics/sales-by-category`` response."""

    model_config = ConfigDict(populate_by_name=True)

    currency: str
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    category: str | None = None
    brand: str | None = None
    model: str | None = None

    categories: list[CategoryTotal]
    rows: list[CategorySalesRow]

    total_qty: int
    total_revenue: Decimal
    total_revenue_by_currency: dict[str, Decimal]
