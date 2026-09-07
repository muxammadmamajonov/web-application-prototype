"""Platform HQ: the console dashboard and its cross-tenant payment view."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.currency import (
    DEFAULT_CURRENCY,
    ints_by_currency,
    money_by_currency,
)
from app.core.security import current_user
from app.db import get_db
from app.models import (
    Customer,
    Installment,
    Payment,
    Plan,
    Tenant,
    User,
)
from app.models.enums import (
    InstallmentStatus,
    PaymentMethod,
    PaymentStatus,
    TenantStatus,
)
from app.schemas.common import LimitQ

from ._shared import (
    _require_platform,
)

router = APIRouter()

@router.get("/overview")
async def overview(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_platform(user)

    tenants_total = (await db.execute(select(func.count(Tenant.id)))).scalar_one()
    by_status_rows = (await db.execute(
        select(Tenant.status, func.count(Tenant.id)).group_by(Tenant.status)
    )).all()
    by_status = {row[0].value: row[1] for row in by_status_rows}

    active_installments = (
        await db.execute(
            select(func.count(Installment.id)).where(Installment.status == InstallmentStatus.active)
        )
    ).scalar_one()
    pending_receipts = (
        await db.execute(
            select(func.count(Payment.id)).where(Payment.status == PaymentStatus.pending)
        )
    ).scalar_one()
    mrr_estimate = (
        await db.execute(
            select(func.coalesce(func.sum(Plan.price_monthly), 0))
            .select_from(Tenant)
            .join(Plan, Plan.code == Tenant.plan_code)
            .where(Tenant.status == TenantStatus.active)
        )
    ).scalar_one()

    # ── Real money metrics (GMV processed = approved customer payments) ──────
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # First day of the month 6 months back → 7 monthly buckets incl. this one.
    y, m = month_start.year, month_start.month - 6
    while m <= 0:
        m += 12
        y -= 1
    window_start = month_start.replace(year=y, month=m)

    # (MUDD-114) Per currency. This is the platform's headline GMV, summed over
    # EVERY tenant — the widest mix in the product — and the field was literally
    # named `_uzs` while containing USD receipts too.
    approved_by_currency = money_by_currency((await db.execute(
        select(Payment.currency, func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.approved,
            Payment.method != PaymentMethod.late_fee,  # (#121) exclude fee accruals from revenue
            Payment.approved_at.is_not(None),
            Payment.approved_at >= month_start,
        ).group_by(Payment.currency)
    )).all())

    pending_tenant_count = (await db.execute(
        select(func.count(func.distinct(Payment.tenant_id))).where(
            Payment.status == PaymentStatus.pending
        )
    )).scalar_one()

    customers_total = (await db.execute(select(func.count(Customer.id)))).scalar_one()

    month_col = func.date_trunc("month", Payment.approved_at)
    series_rows = (await db.execute(
        select(month_col.label("m"), Payment.currency,
               func.coalesce(func.sum(Payment.amount), 0))
        .where(
            Payment.status == PaymentStatus.approved,
            Payment.method != PaymentMethod.late_fee,  # (#121) exclude fee accruals from GMV
            Payment.approved_at.is_not(None),
            Payment.approved_at >= window_start,
        )
        .group_by("m", Payment.currency)
    )).all()
    by_month: dict[str, list[tuple[str, object]]] = {}
    for dt, cur, amount in series_rows:
        by_month.setdefault(f"{dt.year}-{dt.month:02d}", []).append((cur, amount))
    revenue_series = []
    yy, mm = window_start.year, window_start.month
    for _ in range(7):
        key = f"{yy}-{mm:02d}"
        totals = ints_by_currency(money_by_currency(by_month.get(key, [])))
        revenue_series.append({
            "year": yy, "month": mm,
            # `amount` keeps its key for existing chart code, and now means the
            # platform's reporting currency rather than a sum of mixed units.
            "amount": totals.get(DEFAULT_CURRENCY, 0),
            "amount_by_currency": totals,
        })
        mm += 1
        if mm > 12:
            mm = 1
            yy += 1

    return {
        "tenants_total": tenants_total,
        "tenants_by_status": {
            "active": by_status.get("active", 0),
            "demo": by_status.get("demo", 0),
            "suspended": by_status.get("suspended", 0),
            "blocked": by_status.get("blocked", 0),
        },
        "active_installments": active_installments,
        "pending_receipts": pending_receipts,
        "pending_tenant_count": int(pending_tenant_count or 0),
        "mrr_estimate_uzs": int(mrr_estimate or 0),
        # Still `_uzs`, and now actually UZS: the platform reports in
        # `DEFAULT_CURRENCY`, and any other currency is stated beside it rather
        # than folded in.
        "approved_this_month_uzs": int(approved_by_currency.get(DEFAULT_CURRENCY, 0)),
        "approved_this_month_by_currency": ints_by_currency(approved_by_currency),
        "customers_total": int(customers_total or 0),
        "revenue_series": revenue_series,
    }

@router.get("/payments")
async def platform_payments(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = None,
    limit: LimitQ = 50,
) -> dict:
    """Cross-tenant payment oversight (read-only). Receipt approval stays with
    the tenant; this is the platform's monitoring view + the Overview CTA."""
    _require_platform(user)

    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    collected_by_currency = money_by_currency((await db.execute(
        select(Payment.currency, func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.approved,
            Payment.method != PaymentMethod.late_fee,  # (#121) exclude fee accruals from revenue
            Payment.approved_at.is_not(None),
            Payment.approved_at >= month_start,
        ).group_by(Payment.currency)
    )).all())
    pending_by_currency = money_by_currency((await db.execute(
        select(Payment.currency, func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.status == PaymentStatus.pending)
        .group_by(Payment.currency)
    )).all())
    pending_count = (await db.execute(
        select(func.count(Payment.id)).where(Payment.status == PaymentStatus.pending)
    )).scalar_one()

    q = (
        select(Payment, Tenant.name)
        .join(Tenant, Tenant.id == Payment.tenant_id)
        .order_by(Payment.created_at.desc())
    )
    if status_filter:
        try:
            statuses = [PaymentStatus(s.strip()) for s in status_filter.split(",") if s.strip()]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_status") from None
        if statuses:
            q = q.where(Payment.status.in_(statuses))
    rows = (await db.execute(q.limit(limit))).all()
    items = [
        {
            "id": str(p.id),
            "tenant_id": str(p.tenant_id),
            "tenant_name": tname,
            "amount": int(p.amount or 0),
            # (MUDD-114) Each row states its own currency. The review list mixes
            # every tenant on the platform, so without this a reviewer comparing
            # two rows has no way to know they are not the same unit.
            "currency": p.currency,
            "method": p.method.value,
            "status": p.status.value,
            "receipt_image_url": p.receipt_image_url,
            "created_at": p.created_at.isoformat(),
            "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        }
        for (p, tname) in rows
    ]
    return {
        "collected_this_month_uzs": int(collected_by_currency.get(DEFAULT_CURRENCY, 0)),
        "pending_amount_uzs": int(pending_by_currency.get(DEFAULT_CURRENCY, 0)),
        "collected_this_month_by_currency": ints_by_currency(collected_by_currency),
        "pending_amount_by_currency": ints_by_currency(pending_by_currency),
        "pending_count": int(pending_count or 0),
        "items": items,
    }
