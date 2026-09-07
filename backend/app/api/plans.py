"""Public plan catalogue (Round 26).

Unauthenticated endpoint powering the marketing landing page so pricing comes
from the DB instead of hardcoded JSX. Returns only active plans, ordered by
``sort_order``, with the marketing-relevant fields (price, badge, limits,
capability flags). Secret/internal columns are not exposed.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Plan

router = APIRouter(prefix="/plans", tags=["plans"])


@router.get("/public")
async def public_plans(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    # Only plans flagged for the landing page, in display order.
    rows = (await db.execute(
        select(Plan).where(Plan.is_active.is_(True), Plan.is_public.is_(True))
        .order_by(Plan.sort_order)
    )).scalars().all()
    # The yearly discount is configured once (Platform → Sozlamalar → Asosiy
    # qiymatlar) and derived per plan here, so the landing page never has to know
    # the rule and no second price is stored beside the monthly one.
    from app.services.yearly_billing import get_config

    yearly = await get_config(db)
    return {
        "yearly": {
            "enabled": yearly.enabled,
            "discount_pct": yearly.discount_pct,
        },
        "items": [
            {
                "code": p.code,
                "name_uz": p.name_uz, "name_ru": p.name_ru,
                "description_uz": p.description_uz, "description_ru": p.description_ru,
                "price_monthly": int(p.price_monthly or 0),
                # Derived, never stored: a second stored price is a second thing
                # to keep in step, and the first reprice that forgets it makes
                # the landing page quote a discount that is not real.
                "price_yearly": yearly.price_for(int(p.price_monthly or 0)),
                "price_yearly_monthly_equivalent": yearly.monthly_equivalent(int(p.price_monthly or 0)),
                "yearly_saving": yearly.saving_for(int(p.price_monthly or 0)),
                "price_per_installment": (
                    int(p.price_per_installment) if p.price_per_installment is not None else None
                ),
                "max_active_installments": p.max_active_installments,
                "max_staff": p.max_staff,
                "max_branches": p.max_branches,
                "badge": p.badge,
                "is_popular": p.is_popular,
                "is_contact_sales": p.is_contact_sales,
                "overage": p.overage or {},
                "features": p.features,
                "caps": {
                    "allow_custom_domain": p.allow_custom_domain,
                    "allow_pdf_template_upload": p.allow_pdf_template_upload,
                    "allow_api_keys": p.allow_api_keys,
                    "allow_outbound_webhooks": p.allow_outbound_webhooks,
                    "sms_included_per_month": p.sms_included_per_month,
                    "commission_management": p.commission_management,
                    "multi_branch": p.multi_branch,
                    "risk_network_access": p.risk_network_access,
                    "reports_advanced": p.reports_advanced,
                    "max_custom_fields": p.max_custom_fields,
                    "broadcasts_per_month": p.broadcasts_per_month,
                },
            }
            for p in rows
        ],
    }
