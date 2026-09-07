"""Owner dashboard analytics API (MUDD-134).

``GET /tenant/analytics/summary``            — the single-screen KPI set.
``GET /tenant/analytics/sales-by-category``  — sold qty/value by catalog
category, drillable to brand and model.

Owner/manager only, matching every other analytics surface
(``app/api/reports.py``) — sales staff don't see revenue/margin data.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import report_limit
from app.db import get_db
from app.models import Tenant, TenantUser
from app.services import analytics as svc

from ._shared import _require_owner_or_manager, _tenant_today, resolve_tenant

router = APIRouter(prefix="/analytics", tags=["analytics"])

# (MUDD-134) No `response_model=` and no `-> dict` return annotation here,
# deliberately — matching every sibling analytics endpoint (app/api/reports.py).
# Either one makes FastAPI serialize the response through pydantic instead of
# `jsonable_encoder`, and pydantic's JSON mode turns Decimal into a STRING —
# silently changing every money field's wire type for this endpoint alone,
# versus the number every other money endpoint in this API returns. The shapes
# in app/schemas/analytics.py are the documented contract; the plain dict these
# handlers return, run through jsonable_encoder, is what goes over the wire.


@router.get("/summary", dependencies=[report_limit()])
async def analytics_summary(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    granularity: str = Query(default="day", pattern="^(day|week|month)$"),
):
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    today = _tenant_today(tenant)
    to = date_to or today
    frm = date_from or (to - timedelta(days=30))
    if frm > to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "from_after_to")
    return await svc.owner_summary(
        db, tenant.id, date_from=frm, date_to=to, granularity=granularity, today=today,
    )


@router.get("/sales-by-category", dependencies=[report_limit()])
async def analytics_sales_by_category(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    category: str | None = Query(default=None, max_length=64),
    brand: str | None = Query(default=None, max_length=48),
    model: str | None = Query(default=None, max_length=80),
):
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "from_after_to")
    return await svc.category_sales(
        db, tenant.id, date_from=date_from, date_to=date_to,
        category=category, brand=brand, model=model,
    )
