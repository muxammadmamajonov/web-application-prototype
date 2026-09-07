"""SMS fallback API (Round 25).

Exposes the tenant-facing surface for the Eskiz.uz SMS channel:
  * ``GET  /tenant/sms/usage``   — budget vs month-to-date spend + counts.
  * ``GET  /tenant/sms/history`` — recent outbound SMS log.
  * ``GET  /tenant/customers/{id}/channels`` — per-customer channel prefs.
  * ``PUT  /tenant/customers/{id}/channels`` — replace channel prefs.

Budget/sender editing lives in the tenant settings PATCH (``sms`` group).
Actual sending happens in the reminder cron (``services/scheduler``); this
module is read/config only.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tenant import _require_owner_or_manager, resolve_tenant
from app.db import get_db
from app.models import Customer, NotificationChannel, SmsMessage, Tenant, TenantSettings, TenantUser
from app.models.enums import NotificationChannelType, SmsStatus
from app.schemas.common import LimitQ, OffsetQ
from app.services import sms_eskiz

router = APIRouter(prefix="/tenant", tags=["sms"])


@router.get("/sms/usage")
async def sms_usage(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Owner/manager only — SMS spend is billing data."""
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )).scalar_one_or_none()
    budget = int((s.sms_budget_uzs_per_month if s else 0) or 0)

    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    spent = (await db.execute(
        select(func.coalesce(func.sum(SmsMessage.cost_uzs), Decimal("0"))).where(
            SmsMessage.tenant_id == tenant.id,
            SmsMessage.status == SmsStatus.sent,
            SmsMessage.created_at >= month_start,
        )
    )).scalar_one()
    sent_count = (await db.execute(
        select(func.count(SmsMessage.id)).where(
            SmsMessage.tenant_id == tenant.id,
            SmsMessage.status == SmsStatus.sent,
            SmsMessage.created_at >= month_start,
        )
    )).scalar_one()
    failed_count = (await db.execute(
        select(func.count(SmsMessage.id)).where(
            SmsMessage.tenant_id == tenant.id,
            SmsMessage.status == SmsStatus.failed,
            SmsMessage.created_at >= month_start,
        )
    )).scalar_one()

    spent_i = int(spent or 0)
    return {
        "configured": sms_eskiz.is_configured(),
        "budget_uzs_per_month": budget,
        "spent_this_month": spent_i,
        "remaining": max(0, budget - spent_i),
        "sender": (s.sms_sender_alpha if s else None) or None,
        "sent_count": int(sent_count or 0),
        "failed_count": int(failed_count or 0),
    }


@router.get("/sms/history")
async def sms_history(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: LimitQ = 50,
    offset: OffsetQ = 0,
) -> list[dict]:
    """Owner/manager only — message bodies and recipients are customer PII."""
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    rows = (await db.execute(
        select(SmsMessage)
        .where(SmsMessage.tenant_id == tenant.id)
        .order_by(SmsMessage.created_at.desc(), SmsMessage.id.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    return [
        {
            "id": str(m.id),
            "customer_id": str(m.customer_id) if m.customer_id else None,
            "phone": m.phone,
            "text": m.text,
            "parts": m.parts,
            "cost_uzs": int(m.cost_uzs or 0),
            "status": m.status.value,
            "kind": m.kind,
            "error": m.error,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in rows
    ]


# ── per-customer channel preferences ───────────────────────────────────────

class ChannelIn(BaseModel):
    channel:    Literal["telegram", "sms", "email"]
    is_primary: bool = False
    opted_in:   bool = False


class ChannelsPut(BaseModel):
    channels: list[ChannelIn]


async def _get_customer(db: AsyncSession, tenant: Tenant, cid: UUID) -> Customer:
    c = (await db.execute(
        select(Customer).where(Customer.id == cid, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    return c


def _channel_dict(ch: NotificationChannel) -> dict:
    return {
        "channel": ch.channel.value,
        "is_primary": ch.is_primary,
        "opted_in": ch.opted_in_at is not None,
        "opted_in_at": ch.opted_in_at.isoformat() if ch.opted_in_at else None,
    }


@router.get("/customers/{cid}/channels")
async def list_customer_channels(
    cid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    tenant, _ = ctx
    await _get_customer(db, tenant, cid)
    rows = (await db.execute(
        select(NotificationChannel).where(NotificationChannel.customer_id == cid)
    )).scalars().all()
    return [_channel_dict(ch) for ch in rows]


@router.put("/customers/{cid}/channels")
async def set_customer_channels(
    cid: UUID,
    body: ChannelsPut,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    """Replace the customer's channel preferences with the supplied set."""
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    await _get_customer(db, tenant, cid)

    existing = {
        ch.channel: ch
        for ch in (await db.execute(
            select(NotificationChannel).where(NotificationChannel.customer_id == cid)
        )).scalars().all()
    }
    seen: set[NotificationChannelType] = set()
    now = datetime.now(UTC)
    for item in body.channels:
        ctype = NotificationChannelType(item.channel)
        seen.add(ctype)
        row = existing.get(ctype)
        if row is None:
            row = NotificationChannel(tenant_id=tenant.id, customer_id=cid, channel=ctype)
            db.add(row)
        row.is_primary = item.is_primary
        # Preserve the original opt-in timestamp if still opted in.
        if item.opted_in:
            row.opted_in_at = row.opted_in_at or now
        else:
            row.opted_in_at = None
    # Drop channels no longer present in the payload.
    for ctype, row in existing.items():
        if ctype not in seen:
            await db.delete(row)

    await db.commit()
    rows = (await db.execute(
        select(NotificationChannel).where(NotificationChannel.customer_id == cid)
    )).scalars().all()
    return [_channel_dict(ch) for ch in rows]
