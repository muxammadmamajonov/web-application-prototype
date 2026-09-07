"""Platform HQ: promo codes and advertising campaigns."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import current_user
from app.db import get_db
from app.models import (
    User,
)
from app.schemas.common import LimitQ, OffsetQ

from ._shared import (
    _require_platform,
    _require_super,
)

router = APIRouter()

# ── promo codes ────────────────────────────────────────────────────────────

from decimal import Decimal

from app.models import PromoCode


class PromoCodeIn(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    description: str | None = None
    kind: str = Field(pattern="^(percent|fixed)$")
    value: Decimal
    max_uses: int | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None

@router.get("/promos")
async def list_promos(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: LimitQ = 50,
    offset: OffsetQ = 0,
) -> dict:
    _require_platform(user)
    rows = (await db.execute(
        select(PromoCode).order_by(PromoCode.created_at.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    return {
        "items": [
            {
                "id": str(p.id),
                "code": p.code,
                "description": p.description,
                "kind": p.kind,
                "value": float(p.value or 0),
                "max_uses": p.max_uses,
                "used_count": p.used_count,
                "valid_from": p.valid_from.isoformat() if p.valid_from else None,
                "valid_until": p.valid_until.isoformat() if p.valid_until else None,
                "is_active": p.is_active,
            }
            for p in rows
        ],
    }

@router.post("/promos", status_code=status.HTTP_201_CREATED)
async def create_promo(
    body: PromoCodeIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    promo = PromoCode(**body.model_dump(exclude_unset=True))
    db.add(promo)
    await db.commit()
    await db.refresh(promo)
    return {"id": str(promo.id), "code": promo.code}

class PromoPatchIn(BaseModel):
    description: str | None = None
    kind: str | None = Field(default=None, pattern="^(percent|fixed)$")
    value: Decimal | None = None
    max_uses: int | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    is_active: bool | None = None

@router.patch("/promos/{promo_id}")
async def update_promo(
    promo_id: str,
    body: PromoPatchIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    promo = (await db.execute(select(PromoCode).where(PromoCode.id == promo_id))).scalar_one_or_none()
    if promo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "promo_not_found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(promo, k, v)
    await db.commit()
    return {"id": str(promo.id), "code": promo.code, "is_active": promo.is_active}

@router.delete("/promos/{promo_id}", status_code=status.HTTP_200_OK)
async def delete_promo(
    promo_id: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    promo = (await db.execute(select(PromoCode).where(PromoCode.id == promo_id))).scalar_one_or_none()
    if promo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "promo_not_found")
    await db.delete(promo)
    await db.commit()
    return {"deleted": str(promo_id)}

# ── ad campaigns ───────────────────────────────────────────────────────────

from app.models import AdCampaign


@router.get("/ads")
async def list_ads(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: LimitQ = 50,
    offset: OffsetQ = 0,
) -> dict:
    _require_platform(user)
    rows = (await db.execute(
        select(AdCampaign).order_by(AdCampaign.created_at.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    return {
        "items": [
            {
                "id": str(a.id),
                "name": a.name,
                "channel": a.channel,
                "status": a.status,
                "budget": int(a.budget or 0),
                "spent": int(a.spent or 0),
                "leads_count": a.leads_count,
                "converted_tenants": a.converted_tenants,
                "started_at": a.started_at.isoformat() if a.started_at else None,
                "ended_at": a.ended_at.isoformat() if a.ended_at else None,
                "notes": a.notes,
            }
            for a in rows
        ],
    }

class AdCampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    channel: str = Field(min_length=2, max_length=40)
    status: str = Field(default="draft", pattern="^(draft|active|paused|ended)$")
    budget: int = 0
    spent: int = 0
    notes: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

@router.post("/ads", status_code=status.HTTP_201_CREATED)
async def create_ad(
    body: AdCampaignIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    ad = AdCampaign(**body.model_dump(exclude_unset=True))
    db.add(ad)
    await db.commit()
    await db.refresh(ad)
    return {"id": str(ad.id), "name": ad.name}

class AdCampaignPatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    channel: str | None = Field(default=None, max_length=40)
    status: str | None = Field(default=None, pattern="^(draft|active|paused|ended)$")
    budget: int | None = None
    spent: int | None = None
    leads_count: int | None = None
    converted_tenants: int | None = None
    notes: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

@router.patch("/ads/{ad_id}")
async def update_ad(
    ad_id: str,
    body: AdCampaignPatchIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    ad = (await db.execute(select(AdCampaign).where(AdCampaign.id == ad_id))).scalar_one_or_none()
    if ad is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ad_not_found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(ad, k, v)
    await db.commit()
    return {"id": str(ad.id), "name": ad.name, "status": ad.status}

@router.delete("/ads/{ad_id}", status_code=status.HTTP_200_OK)
async def delete_ad(
    ad_id: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    ad = (await db.execute(select(AdCampaign).where(AdCampaign.id == ad_id))).scalar_one_or_none()
    if ad is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ad_not_found")
    await db.delete(ad)
    await db.commit()
    return {"deleted": str(ad_id)}
