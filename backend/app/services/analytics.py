"""Owner dashboard analytics (MUDD-134).

Two read-only aggregations, both per-tenant and per-currency (MUDD-114 —
see ``app.core.currency`` for why nothing is ever converted or summed
across currencies):

* ``owner_summary``   — the single-screen KPI set: revenue/collections,
  outstanding debt, overdue + upcoming payments, a cash-flow trend, contract
  funnel counts, customer count, inventory, and notification delivery health.
* ``category_sales``  — sold qty/value grouped by catalog category, drillable
  to brand and model via the ``category``/``brand``/``model`` filters.

Every function returns plain JSON-friendly dicts, matching the convention in
``app.services.reports`` (which this module composes for the cash-flow trend
rather than re-deriving it).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import local_day_bounds_utc, tenant_zone
from app.core.currency import money_by_currency, primary_total, tenant_primary_currency
from app.models import (
    CatalogBrand,
    CatalogCategory,
    CatalogModel,
    Customer,
    Installment,
    InstallmentItem,
    Notification,
    Product,
    SmsMessage,
)
from app.models.enums import InstallmentStatus, SmsStatus
from app.services import reports as reports_svc

ZERO = Decimal("0")
_ACTIVE = (InstallmentStatus.active, InstallmentStatus.overdue)


def _outstanding_col():
    """Same definition as ``reports._outstanding_col`` — remaining financed
    balance, never negative. Not imported from there to avoid depending on a
    private name across modules; the arithmetic itself lives once, on
    ``Installment.remaining_balance`` (see app/models/installment.py)."""
    return func.greatest(Installment.principal - Installment.paid_amount, ZERO)


# ── owner summary ────────────────────────────────────────────────────────────

async def owner_summary(
    db: AsyncSession, tenant_id: UUID, *,
    date_from: date, date_to: date, granularity: str = "day",
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    primary = await tenant_primary_currency(db, tenant_id)
    # (MUDD-140) The same per-tenant business day the cash-flow trend below
    # uses — a notification sent at 01:00 Asia/Tashkent must fall in the same
    # window as the payment it followed, not one bucketed by session UTC.
    tz = await tenant_zone(db, tenant_id)
    start_dt, end_dt = local_day_bounds_utc(date_from, date_to, tz)

    # Revenue / collections + trend: composed from the existing, already
    # currency-safe cashflow report rather than re-deriving the definition of
    # "collected cash" (which excludes late-fee accrual rows — see
    # reports.cashflow) a second time.
    cf = await reports_svc.cashflow(
        db, tenant_id, date_from=date_from, date_to=date_to, granularity=granularity,
    )

    out = _outstanding_col()
    debt_rows = (await db.execute(
        select(Installment.currency, func.coalesce(func.sum(out), ZERO))
        .where(Installment.tenant_id == tenant_id, Installment.status.in_(_ACTIVE))
        .group_by(Installment.currency)
    )).all()
    debt_by_currency = money_by_currency(debt_rows)

    overdue_rows = (await db.execute(
        select(Installment.currency, func.count(Installment.id),
               func.coalesce(func.sum(out), ZERO))
        .where(Installment.tenant_id == tenant_id, Installment.status == InstallmentStatus.overdue)
        .group_by(Installment.currency)
    )).all()
    overdue_count = sum(int(c) for _, c, _ in overdue_rows)
    overdue_by_currency = money_by_currency([(cur, amt) for cur, _, amt in overdue_rows])

    # Upcoming: due inside the caller's window but not yet overdue — the
    # overdue bucket above already covers "already late", so this is strictly
    # the forward-looking part of the same funnel.
    upcoming_rows = (await db.execute(
        select(Installment.currency, func.count(Installment.id),
               func.coalesce(func.sum(Installment.monthly_amount), ZERO))
        .where(
            Installment.tenant_id == tenant_id,
            Installment.status == InstallmentStatus.active,
            Installment.next_due_date >= today,
            Installment.next_due_date <= date_to,
        )
        .group_by(Installment.currency)
    )).all()
    upcoming_count = sum(int(c) for _, c, _ in upcoming_rows)
    upcoming_by_currency = money_by_currency([(cur, amt) for cur, _, amt in upcoming_rows])

    status_rows = (await db.execute(
        select(Installment.status, func.count(Installment.id))
        .where(Installment.tenant_id == tenant_id)
        .group_by(Installment.status)
    )).all()
    contracts_by_status = {s.value: int(c) for s, c in status_rows}

    customers_count = (await db.execute(
        select(func.count(Customer.id)).where(Customer.tenant_id == tenant_id)
    )).scalar_one()

    # Inventory: Product carries no currency column of its own (unlike
    # Installment/Payment, which snapshot theirs at creation) — a shop's stock
    # is always priced in whatever currency is CURRENTLY configured, so this is
    # a single scalar, not a by_currency map.
    stock_col = func.coalesce(Product.stock, 0)
    inv_row = (await db.execute(
        select(
            func.count(Product.id),
            func.coalesce(func.sum(stock_col), 0),
            func.coalesce(
                func.sum(case(
                    (Product.cost_price.is_not(None), Product.cost_price * stock_col),
                    else_=ZERO,
                )), ZERO,
            ),
            func.coalesce(
                func.sum(case((Product.cost_price.is_(None), 1), else_=0)), 0,
            ),
        ).where(Product.tenant_id == tenant_id, Product.is_active.is_(True))
    )).one()
    active_products_count, units_in_stock, inventory_value, missing_cost = inv_row

    # Notification delivery health, per channel, over the same window.
    # Telegram: `Notification` rows ARE the delivery attempt (see
    # app.services.notifications) — delivered_at set = sent, attempt made but
    # not delivered = failed, never attempted = pending.
    tg_row = (await db.execute(
        select(
            func.coalesce(func.sum(case((Notification.delivered_at.is_not(None), 1), else_=0)), 0),
            func.coalesce(func.sum(case(
                (Notification.delivered_at.is_(None) & (Notification.attempt_count > 0), 1),
                else_=0,
            )), 0),
            func.coalesce(func.sum(case((Notification.attempt_count == 0, 1), else_=0)), 0),
        ).where(
            Notification.tenant_id == tenant_id,
            Notification.created_at >= start_dt, Notification.created_at < end_dt,
        )
    )).one()
    sms_rows = (await db.execute(
        select(SmsMessage.status, func.count(SmsMessage.id))
        .where(
            SmsMessage.tenant_id == tenant_id,
            SmsMessage.created_at >= start_dt, SmsMessage.created_at < end_dt,
        )
        .group_by(SmsMessage.status)
    )).all()
    sms_by_status = {s.value: int(c) for s, c in sms_rows}

    notifications = [
        {
            "channel": "telegram",
            "sent": int(tg_row[0]), "failed": int(tg_row[1]), "pending": int(tg_row[2]),
        },
        {
            "channel": "sms",
            "sent": sms_by_status.get(SmsStatus.sent.value, 0),
            "failed": sms_by_status.get(SmsStatus.failed.value, 0),
            "pending": sms_by_status.get(SmsStatus.pending.value, 0),
        },
    ]

    return {
        "tenant_id": str(tenant_id),
        "primary_currency": primary,
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "revenue_by_currency": cf["total_by_currency"],
        "revenue": cf["total"],
        "debt_by_currency": debt_by_currency,
        "debt": primary_total(debt_by_currency, primary),
        "overdue_by_currency": overdue_by_currency,
        "overdue": primary_total(overdue_by_currency, primary),
        "overdue_count": overdue_count,
        "upcoming_by_currency": upcoming_by_currency,
        "upcoming": primary_total(upcoming_by_currency, primary),
        "upcoming_count": upcoming_count,
        "cashflow": cf,
        "contracts_by_status": contracts_by_status,
        "customers_count": customers_count,
        "inventory": {
            "currency": primary,
            "active_products_count": int(active_products_count),
            "units_in_stock": int(units_in_stock),
            "value": inventory_value,
            "products_missing_cost": int(missing_cost),
        },
        "notifications": notifications,
    }


# ── category / brand / model sales ──────────────────────────────────────────

_UNCATEGORIZED = "__uncategorized__"  # internal grouping key only; never serialized


async def category_sales(
    db: AsyncSession, tenant_id: UUID, *,
    date_from: date | None = None, date_to: date | None = None,
    category: str | None = None, brand: str | None = None, model: str | None = None,
) -> dict[str, Any]:
    primary = await tenant_primary_currency(db, tenant_id)

    base_filters = [
        Installment.tenant_id == tenant_id,
        Installment.status != InstallmentStatus.cancelled,
    ]
    if date_from is not None:
        base_filters.append(Installment.start_date >= date_from)
    if date_to is not None:
        base_filters.append(Installment.start_date <= date_to)

    # ── category rollup: EVERY catalog category, zero-filled, unaffected by
    # the brand/model filters — this is the top-level chart that must never
    # silently omit a category the platform knows about (MUDD-134 ask).
    cat_rows = (await db.execute(
        select(InstallmentItem.category, Installment.currency,
               func.coalesce(func.sum(InstallmentItem.qty), 0),
               func.coalesce(func.sum(InstallmentItem.line_total), ZERO))
        .select_from(InstallmentItem)
        .join(Installment, Installment.id == InstallmentItem.installment_id)
        .where(*base_filters)
        .group_by(InstallmentItem.category, Installment.currency)
    )).all()
    cat_acc: dict[str, dict[str, Any]] = {}
    for slug, cur, qty, rev in cat_rows:
        key = slug or _UNCATEGORIZED
        entry = cat_acc.setdefault(key, {"qty": 0, "pairs": []})
        entry["qty"] += int(qty or 0)
        entry["pairs"].append((cur, rev))

    all_categories = (await db.execute(
        select(CatalogCategory.slug, CatalogCategory.name_uz, CatalogCategory.name_ru)
        .order_by(CatalogCategory.sort_order, CatalogCategory.slug)
    )).all()
    categories: list[dict[str, Any]] = []
    for slug, name_uz, name_ru in all_categories:
        entry = cat_acc.pop(slug, {"qty": 0, "pairs": []})
        totals = money_by_currency(entry["pairs"])
        categories.append({
            "category_slug": slug, "category_name_uz": name_uz, "category_name_ru": name_ru,
            "qty": entry["qty"], "revenue_by_currency": totals,
            "revenue": primary_total(totals, primary),
        })
    # Sales whose item never resolved to a catalog category at all — kept as a
    # bucket rather than dropped (MUDD-134: "do not silently drop" legacy rows).
    uncategorized = cat_acc.pop(_UNCATEGORIZED, None)
    if uncategorized is not None:
        totals = money_by_currency(uncategorized["pairs"])
        categories.append({
            "category_slug": None, "category_name_uz": None, "category_name_ru": None,
            "qty": uncategorized["qty"], "revenue_by_currency": totals,
            "revenue": primary_total(totals, primary),
        })
    categories.sort(key=lambda c: c["revenue"], reverse=True)

    # ── filtered rows: (category, brand, model), catalog-joined. An item with
    # no `catalog_model_id` (legacy free-text `products.category`) still has
    # its own `InstallmentItem.category` snapshot and lands here with
    # brand/model = None instead of being excluded.
    row_filters = list(base_filters)
    if category is not None:
        row_filters.append(InstallmentItem.category == category)
    brand_join_on = CatalogModel.id == InstallmentItem.catalog_model_id
    row_query = (
        select(
            InstallmentItem.category, CatalogBrand.slug, CatalogBrand.name,
            CatalogModel.slug, CatalogModel.name, Installment.currency,
            func.coalesce(func.sum(InstallmentItem.qty), 0),
            func.coalesce(func.sum(InstallmentItem.line_total), ZERO),
        )
        .select_from(InstallmentItem)
        .join(Installment, Installment.id == InstallmentItem.installment_id)
        .outerjoin(CatalogModel, brand_join_on)
        .outerjoin(CatalogBrand, CatalogBrand.id == CatalogModel.brand_id)
        .where(*row_filters)
    )
    if brand is not None:
        row_query = row_query.where(CatalogBrand.slug == brand)
    if model is not None:
        row_query = row_query.where(CatalogModel.slug == model)
    row_query = row_query.group_by(
        InstallmentItem.category, CatalogBrand.slug, CatalogBrand.name,
        CatalogModel.slug, CatalogModel.name, Installment.currency,
    )
    raw_rows = (await db.execute(row_query)).all()

    row_acc: dict[tuple[Any, ...], dict[str, Any]] = {}
    for cat_slug, b_slug, b_name, m_slug, m_name, cur, qty, rev in raw_rows:
        key = (cat_slug, b_slug, m_slug)
        entry = row_acc.setdefault(key, {
            "category_slug": cat_slug, "brand_slug": b_slug, "brand_name": b_name,
            "model_slug": m_slug, "model_name": m_name, "qty": 0, "pairs": [],
        })
        entry["qty"] += int(qty or 0)
        entry["pairs"].append((cur, rev))

    cat_names = {slug: (u, r) for slug, u, r in all_categories}
    rows: list[dict[str, Any]] = []
    for entry in row_acc.values():
        totals = money_by_currency(entry.pop("pairs"))
        name_uz, name_ru = cat_names.get(entry["category_slug"], (None, None))
        rows.append({
            **entry,
            "category_name_uz": name_uz, "category_name_ru": name_ru,
            "revenue_by_currency": totals, "revenue": primary_total(totals, primary),
        })
    rows.sort(key=lambda r: r["revenue"], reverse=True)

    total_qty = sum(r["qty"] for r in rows)
    total_revenue_by_currency = money_by_currency([
        (code, total) for r in rows for code, total in r["revenue_by_currency"].items()
    ])

    return {
        "currency": primary,
        "from": date_from.isoformat() if date_from else None,
        "to": date_to.isoformat() if date_to else None,
        "category": category, "brand": brand, "model": model,
        "categories": categories,
        "rows": rows,
        "total_qty": total_qty,
        "total_revenue": primary_total(total_revenue_by_currency, primary),
        "total_revenue_by_currency": total_revenue_by_currency,
    }
