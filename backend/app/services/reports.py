"""Owner-facing analytics (Round 24).

Six read-only aggregations over a single tenant's installments / payments:

* ``aging``             — outstanding balance bucketed by days overdue
* ``cashflow``          — approved payment inflow per period
* ``staff_performance`` — per-seller sales count / volume / commission
* ``products``          — sales broken down by brand & category
* ``ltv``               — top customers by lifetime contract value
* ``margin``            — revenue vs known cost_price → gross profit

Every function returns plain JSON-friendly dicts (Decimals are fine — the
API layer's ``jsonable_encoder`` turns them into numbers). Heavy lifting is
SQL-side; we never pull whole tables into Python.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import String, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import local_day_bounds_utc, tenant_zone
from app.core.currency import (
    money_by_currency,
    primary_total,
    tenant_primary_currency,
)
from app.models import (
    Customer,
    Installment,
    InstallmentItem,
    Payment,
    Product,
    TenantSettings,
    TenantUser,
    User,
)
from app.models.enums import InstallmentStatus, PaymentMethod, PaymentStatus

ZERO = Decimal("0")
_ACTIVE = (InstallmentStatus.active, InstallmentStatus.overdue)


#: (MUDD-114) Every report below is denominated PER CURRENCY.
#:
#: A shop's configured currency decides only what the NEXT contract is written
#: in; existing contracts keep the currency they were created with. So one
#: tenant holding both UZS and USD contracts is ordinary, and every aggregate in
#: this module used to add them together — then export the result to CSV, XLSX,
#: PDF and Telegram as a single number.
#:
#: `staff_performance` was the worst of them: it multiplied that mixed sum by a
#: commission rate, so a salesperson's PAYOUT was computed from a quantity that
#: is not an amount of money in any unit.
#:
#: Nothing is converted. There is no exchange-rate source in this product, and a
#: report that invented one would replace a visible wrong number with an
#: invisible one. Each report returns `by_currency`, and the legacy scalar
#: totals are the PRIMARY currency's share — the unit the UI already labels them
#: with — so no client breaks while every figure becomes true.


def _fold(pairs: list[tuple[str, Decimal]]) -> dict[str, Decimal]:
    """Sum already-folded per-currency maps into one. Thin wrapper over
    :func:`money_by_currency` so the report totals use the same primitive as
    the rows they total, rather than a second summation written by hand."""
    return money_by_currency(pairs)


def _outstanding_col():
    """Remaining financed balance = principal − paid_amount (never < 0)."""
    return func.greatest(Installment.principal - Installment.paid_amount, ZERO)


# ── aging ──────────────────────────────────────────────────────────────────

async def aging(db: AsyncSession, tenant_id: UUID, *, today: date | None = None) -> dict:
    today = today or date.today()
    # Bucket by how far past-due ``next_due_date`` is. A null or future due
    # date counts as current (0-30). Boundaries expressed as dates so the
    # comparison stays index-friendly.
    b1 = today - timedelta(days=30)
    b2 = today - timedelta(days=60)
    b3 = today - timedelta(days=90)
    out = _outstanding_col()
    bucket = case(
        (Installment.next_due_date.is_(None), "b0_30"),
        (Installment.next_due_date >= b1, "b0_30"),
        (Installment.next_due_date >= b2, "b31_60"),
        (Installment.next_due_date >= b3, "b61_90"),
        else_="b90_plus",
    ).label("bucket")
    rows = (await db.execute(
        select(bucket, Installment.currency, func.count(Installment.id),
               func.coalesce(func.sum(out), ZERO))
        .where(Installment.tenant_id == tenant_id, Installment.status.in_(_ACTIVE))
        .group_by(bucket, Installment.currency)
    )).all()

    primary = await tenant_primary_currency(db, tenant_id)
    buckets: dict[str, dict[str, Any]] = {
        k: {"count": 0, "outstanding": ZERO, "by_currency": {}}
        for k in ("b0_30", "b31_60", "b61_90", "b90_plus")}
    per_bucket: dict[str, list[tuple[Any, Any]]] = {k: [] for k in buckets}
    for key, cur, cnt, amt in rows:
        buckets[key]["count"] += int(cnt)
        per_bucket[key].append((cur, amt))
    for key, pairs in per_bucket.items():
        totals = money_by_currency(pairs)
        buckets[key]["by_currency"] = totals
        # The bucket's headline figure, in the currency the report is labelled
        # with. Summing across currencies here is what made an ageing bucket
        # unreadable for a mixed shop.
        buckets[key]["outstanding"] = primary_total(totals, primary)
    overall = money_by_currency(
        [(cur, amt) for pairs in per_bucket.values() for cur, amt in pairs]
    )
    total_cnt = sum(b["count"] for b in buckets.values())
    return {
        "as_of": today.isoformat(),
        "currency": primary,
        "buckets": buckets,
        "total_outstanding": primary_total(overall, primary),
        "total_outstanding_by_currency": overall,
        "total_count": total_cnt,
    }


# ── cashflow ─────────────────────────────────────────────────────────────────

_TRUNC = {"day": "day", "week": "week", "month": "month"}


async def cashflow(
    db: AsyncSession, tenant_id: UUID, *,
    date_from: date, date_to: date, granularity: str = "day",
) -> dict:
    gran = _TRUNC.get(granularity, "day")
    tz = await tenant_zone(db, tenant_id)
    when = func.coalesce(Payment.paid_at, Payment.approved_at, Payment.created_at)
    # (MUDD-140) `when` is `timestamptz`; truncating it directly buckets by the
    # DB SESSION's zone (UTC — there is no `SET TIME ZONE` anywhere in
    # `app.db`), so a payment made at 01:00 Asia/Tashkent landed on the
    # PREVIOUS day's bar. `timezone(tz, ts)` converts to a naive local
    # timestamp first, so `date_trunc` truncates the tenant's own calendar day.
    local_when = func.timezone(tz.key, when)
    period = func.date_trunc(gran, local_when).label("period")
    start_dt, end_dt = local_day_bounds_utc(date_from, date_to, tz)
    rows = (await db.execute(
        select(period, Payment.currency, func.count(Payment.id),
               func.coalesce(func.sum(Payment.amount), ZERO))
        .where(
            Payment.tenant_id == tenant_id,
            Payment.status == PaymentStatus.approved,
            Payment.method != PaymentMethod.late_fee,  # (#121) accrual, not cash received
            when >= start_dt,
            when < end_dt,
        )
        .group_by(period, Payment.currency)
        .order_by(period)
    )).all()
    primary = await tenant_primary_currency(db, tenant_id)
    # One entry per period, currencies folded inside it — a chart plots a line
    # per unit, and a period that silently merged UZS and USD receipts would
    # draw a spike that never happened.
    by_period: dict[str, dict[str, Any]] = {}
    for p, cur, c, a in rows:
        key = p.date().isoformat() if isinstance(p, datetime) else str(p)
        entry = by_period.setdefault(key, {"period": key, "count": 0, "_pairs": []})
        entry["count"] += int(c)
        entry["_pairs"].append((cur, a))
    series = []
    for entry in by_period.values():
        totals = money_by_currency(entry.pop("_pairs"))
        entry["by_currency"] = totals
        entry["amount"] = primary_total(totals, primary)
        series.append(entry)
    series.sort(key=lambda e: e["period"])
    overall = money_by_currency(
        [(cur, amt) for _, cur, _, amt in rows]
    )
    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "granularity": gran,
        "currency": primary,
        "series": series,
        "total": primary_total(overall, primary),
        "total_by_currency": overall,
    }


# ── staff performance ────────────────────────────────────────────────────────

async def staff_performance(db: AsyncSession, tenant_id: UUID) -> dict:
    settings = (await db.execute(
        select(TenantSettings.commission_rate_pct).where(TenantSettings.tenant_id == tenant_id)
    )).scalar_one_or_none()
    default_rate = settings if settings is not None else ZERO

    rows = (await db.execute(
        select(
            Installment.sold_by_user_id,
            Installment.currency,
            func.count(Installment.id),
            func.coalesce(func.sum(Installment.total_amount), ZERO),
        )
        .where(Installment.tenant_id == tenant_id,
               Installment.status != InstallmentStatus.cancelled)
        .group_by(Installment.sold_by_user_id, Installment.currency)
    )).all()

    # Resolve names + per-staff overrides in one pass.
    overrides: dict[UUID, Decimal | None] = {
        user_id: rate
        for user_id, rate in (await db.execute(
            select(TenantUser.user_id, TenantUser.commission_rate_pct)
            .where(TenantUser.tenant_id == tenant_id)
        )).all()
    }
    names: dict[UUID, str] = {
        uid: name
        for uid, name in (await db.execute(
            select(User.id, func.coalesce(User.first_name, User.telegram_username, cast(User.id, String)))
            .join(TenantUser, TenantUser.user_id == User.id)
            .where(TenantUser.tenant_id == tenant_id)
        )).all()
    }

    primary = await tenant_primary_currency(db, tenant_id)

    # (MUDD-114) This is the one report where mixing currencies did not merely
    # mislead — it produced a PAYOUT. `volume` was a cross-currency sum, and
    # multiplying it by the commission rate yielded money owed to a real person,
    # computed from a number that is not an amount in any unit.
    #
    # Commission is now computed PER CURRENCY and paid in the currency of the
    # sale, which is the only arithmetic that is defined without a rate source.
    per_staff: dict[Any, dict[str, Any]] = {}
    for uid, cur, cnt, volume in rows:
        entry = per_staff.setdefault(uid, {"sales_count": 0, "pairs": []})
        entry["sales_count"] += int(cnt)
        entry["pairs"].append((cur, volume))

    staff: list[dict[str, Any]] = []
    for uid, entry in per_staff.items():
        rate = (overrides.get(uid) if uid else None)
        rate = rate if rate is not None else default_rate
        volume_by_currency = money_by_currency(entry["pairs"])
        commission_by_currency = {
            code: total * (rate / Decimal("100"))
            for code, total in volume_by_currency.items()
        }
        staff.append({
            "user_id": str(uid) if uid else None,
            "name": names.get(uid, "—") if uid else "—",
            "sales_count": entry["sales_count"],
            # Legacy scalars: the primary currency's share, matching the label
            # the payroll screen already prints beside them.
            "volume": primary_total(volume_by_currency, primary),
            "commission_rate_pct": rate,
            "commission": commission_by_currency.get(primary, ZERO),
            "volume_by_currency": volume_by_currency,
            "commission_by_currency": commission_by_currency,
        })
    # Ranked within the primary currency: "who sold most" has an answer only
    # inside one unit. A seller working solely in a secondary currency still
    # appears, after the ranked ones, with their own figures intact.
    staff.sort(key=lambda s: s["volume"], reverse=True)
    return {
        "default_rate_pct": default_rate,
        "currency": primary,
        "staff": staff,
        # Totals stay in the primary currency (the per-row scalars they sum are
        # already narrowed to it), with the full picture beside them.
        "total_volume": sum((s["volume"] for s in staff), ZERO),
        "total_commission": sum((s["commission"] for s in staff), ZERO),
        "total_volume_by_currency": _fold([
            (code, total)
            for row in staff for code, total in row["volume_by_currency"].items()
        ]),
        "total_commission_by_currency": _fold([
            (code, total)
            for row in staff for code, total in row["commission_by_currency"].items()
        ]),
    }


# ── products ──────────────────────────────────────────────────────────────────

async def products(db: AsyncSession, tenant_id: UUID) -> dict:
    brand = func.coalesce(Product.brand, "—").label("brand")
    category = func.coalesce(Product.category, "—").label("category")
    rows = (await db.execute(
        select(
            brand, category, Installment.currency,
            func.coalesce(func.sum(InstallmentItem.qty), 0),
            func.coalesce(func.sum(InstallmentItem.line_total), ZERO),
        )
        .select_from(InstallmentItem)
        .join(Installment, Installment.id == InstallmentItem.installment_id)
        .outerjoin(Product, Product.id == InstallmentItem.product_id)
        .where(Installment.tenant_id == tenant_id,
               Installment.status != InstallmentStatus.cancelled)
        .group_by(brand, category, Installment.currency)
    )).all()
    primary = await tenant_primary_currency(db, tenant_id)
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for b, c, cur, q, rev in rows:
        entry = acc.setdefault((b, c), {"brand": b, "category": c, "qty": 0, "_pairs": []})
        entry["qty"] += int(q)
        entry["_pairs"].append((cur, rev))
    items = []
    for entry in acc.values():
        totals = money_by_currency(entry.pop("_pairs"))
        entry["revenue_by_currency"] = totals
        entry["revenue"] = primary_total(totals, primary)
        items.append(entry)
    items.sort(key=lambda i: i["revenue"], reverse=True)
    return {
        "currency": primary,
        "rows": items,
        "total_revenue": sum((i["revenue"] for i in items), ZERO),
        "total_revenue_by_currency": _fold([
            (code, total)
            for i in items for code, total in i["revenue_by_currency"].items()
        ]),
        "total_qty": sum(i["qty"] for i in items),
    }


# ── customer LTV ──────────────────────────────────────────────────────────────

async def ltv(db: AsyncSession, tenant_id: UUID, *, limit: int = 20) -> dict:
    # The LIMIT now applies to CUSTOMERS, not to (customer, currency) rows — the
    # per-currency split multiplies the row count, and limiting the raw rows
    # would have silently returned fewer than `limit` customers for a mixed
    # shop. Ranked by the primary currency's lifetime value, since "top
    # customers" is only orderable inside one unit.
    primary = await tenant_primary_currency(db, tenant_id)
    ranked = (await db.execute(
        select(Installment.customer_id)
        .where(Installment.tenant_id == tenant_id,
               Installment.status != InstallmentStatus.cancelled)
        .group_by(Installment.customer_id)
        # Ranked on the PRIMARY currency only. Ordering by a cross-currency sum
        # would decide *which customers appear at all* from a number that is not
        # an amount — a mixed shop's "top 20" would be the top 20 of nothing.
        .order_by(func.coalesce(func.sum(
            case((Installment.currency == primary, Installment.total_amount), else_=ZERO)
        ), ZERO).desc())
        .limit(limit)
    )).scalars().all()
    if not ranked:
        return {"limit": limit, "currency": primary, "customers": []}
    rows = (await db.execute(
        select(
            Customer.id, Customer.full_name, Customer.phone, Installment.currency,
            func.count(Installment.id),
            func.coalesce(func.sum(Installment.total_amount), ZERO),
            func.coalesce(func.sum(Installment.paid_amount), ZERO),
        )
        .join(Installment, Installment.customer_id == Customer.id)
        .where(Installment.tenant_id == tenant_id,
               Installment.status != InstallmentStatus.cancelled,
               Customer.id.in_(ranked))
        .group_by(Customer.id, Customer.full_name, Customer.phone, Installment.currency)
    )).all()
    acc: dict[Any, dict[str, Any]] = {}
    for cid, name, phone, cur, cnt, total, paid in rows:
        entry = acc.setdefault(cid, {
            "customer_id": str(cid), "name": name, "phone": phone,
            "contracts": 0, "_ltv": [], "_paid": [],
        })
        entry["contracts"] += int(cnt)
        entry["_ltv"].append((cur, total))
        entry["_paid"].append((cur, paid))
    customers = []
    for entry in acc.values():
        ltv_totals = money_by_currency(entry.pop("_ltv"))
        paid_totals = money_by_currency(entry.pop("_paid"))
        entry["lifetime_value"] = primary_total(ltv_totals, primary)
        entry["paid"] = primary_total(paid_totals, primary)
        entry["lifetime_value_by_currency"] = ltv_totals
        entry["paid_by_currency"] = paid_totals
        customers.append(entry)
    customers.sort(key=lambda c: c["lifetime_value"], reverse=True)
    return {
        "limit": limit,
        "currency": primary,
        "customers": customers,
    }


# ── margin ──────────────────────────────────────────────────────────────────

async def margin(db: AsyncSession, tenant_id: UUID) -> dict:
    # Revenue across all sold items; cost only where the linked product has a
    # known cost_price (items without a product or cost contribute 0 cost and
    # are counted separately so the owner knows coverage).
    revenue = func.coalesce(func.sum(InstallmentItem.line_total), ZERO)
    cost = func.coalesce(
        func.sum(case((Product.cost_price.isnot(None),
                       Product.cost_price * InstallmentItem.qty), else_=ZERO)), ZERO)
    known = func.coalesce(
        func.sum(case((Product.cost_price.isnot(None), InstallmentItem.qty), else_=0)), 0)
    total_qty = func.coalesce(func.sum(InstallmentItem.qty), 0)
    rows = (await db.execute(
        select(Installment.currency, revenue, cost, known, total_qty)
        .select_from(InstallmentItem)
        .join(Installment, Installment.id == InstallmentItem.installment_id)
        .outerjoin(Product, Product.id == InstallmentItem.product_id)
        .where(Installment.tenant_id == tenant_id,
               Installment.status != InstallmentStatus.cancelled)
        .group_by(Installment.currency)
    )).all()
    primary = await tenant_primary_currency(db, tenant_id)
    rev_totals = money_by_currency([(cur, r) for cur, r, _, _, _ in rows])
    cost_totals = money_by_currency([(cur, c) for cur, _, c, _, _ in rows])
    known_qty = sum(int(k or 0) for _, _, _, k, _ in rows)
    all_qty = sum(int(q or 0) for _, _, _, _, q in rows)

    # Margin is a RATIO, so it survives per-currency but not across: revenue and
    # cost must be in the same unit or the percentage is meaningless. Computed
    # per currency; the scalar is the primary currency's, matching the label.
    profit_totals = {
        code: total - cost_totals.get(code, ZERO) for code, total in rev_totals.items()
    }
    margin_by_currency = {
        code: (profit_totals[code] / rev_totals[code] * Decimal("100"))
        for code in rev_totals if rev_totals[code]
    }
    rev = primary_total(rev_totals, primary)
    cst = primary_total(cost_totals, primary)
    profit = rev - cst
    return {
        "currency": primary,
        "revenue": rev,
        "cost_known": cst,
        "gross_profit": profit,
        "margin_pct": (profit / rev * Decimal("100")) if rev else ZERO,
        "revenue_by_currency": rev_totals,
        "cost_known_by_currency": cost_totals,
        "gross_profit_by_currency": profit_totals,
        "margin_pct_by_currency": margin_by_currency,
        "qty_with_cost": int(known_qty or 0),
        "qty_total": int(all_qty or 0),
    }
