"""Platform HQ: the referral programme, the platform settings listing and the
cross-tenant risk-network statistics."""
from __future__ import annotations

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import current_user
from app.db import get_db
from app.models import (
    Tenant,
    User,
)
from app.schemas.common import LimitQ, OffsetQ

from ._shared import (
    _display_name,
    _require_platform,
    _require_super,
)

router = APIRouter()

# ── referrals ──────────────────────────────────────────────────────────────

from app.models import Referral


@router.get("/referrals")
async def list_referrals(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: LimitQ = 50,
    offset: OffsetQ = 0,
) -> dict:
    _require_platform(user)
    rows = (await db.execute(
        select(Referral).order_by(Referral.created_at.desc())
        .offset(offset).limit(limit)
    )).scalars().all()

    # Resolve human-readable names so the UI shows people/businesses, not UUIDs.
    user_ids = {r.referrer_user_id for r in rows if r.referrer_user_id}
    tenant_ids = {r.referee_tenant_id for r in rows if r.referee_tenant_id}
    users: dict = {}
    tenants: dict = {}
    if user_ids:
        for u in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars():
            users[u.id] = u
    if tenant_ids:
        for tn in (await db.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))).scalars():
            tenants[tn.id] = tn

    return {
        "items": [
            {
                "id": str(r.id),
                "referrer_user_id": str(r.referrer_user_id),
                "referrer_name": _display_name(users.get(r.referrer_user_id)),
                "referee_tenant_id": str(r.referee_tenant_id) if r.referee_tenant_id else None,
                "referee_name": tenants[r.referee_tenant_id].name if (r.referee_tenant_id and r.referee_tenant_id in tenants) else None,
                "status": r.status,
                "credit_amount": float(r.credit_amount or 0),
                "notes": r.notes,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }

def _referral_share_url(code: str) -> str:
    from app.config import settings
    return f"https://t.me/{settings.telegram_bot_username}?startapp=ref_{code}"

# Short, unambiguous codes (no 0/O/1/l/I) — easy to read and forward in chat.
_REF_ALPHABET = "abcdefghijkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"

_REF_LEN = 8

async def _ensure_ref_code(db: AsyncSession, user: User) -> str:
    """Return the user's stable short referral code, minting one on first use.
    Replaces the old UUID-based deep link (?startapp=ref_<uuid>, 36 chars)."""
    if user.ref_code:
        return user.ref_code
    for _ in range(10):
        code = "".join(secrets.choice(_REF_ALPHABET) for _ in range(_REF_LEN))
        clash = (await db.execute(
            select(User.id).where(User.ref_code == code)
        )).first()
        if not clash:
            user.ref_code = code
            await db.commit()
            return code
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "ref_code_mint_failed")

@router.get("/referrals/my-link")
async def my_referral_link(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Self-invite: the signed-in user's own short, stable referral deep link."""
    _require_platform(user)
    code = await _ensure_ref_code(db, user)
    return {"share_url": _referral_share_url(code)}

class ReferralIn(BaseModel):
    notes: str | None = Field(default=None, max_length=500)

@router.post("/referrals", status_code=status.HTTP_201_CREATED)
async def create_referral(
    body: ReferralIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Return the signed-in user's share link. Idempotent: reuses an existing
    open (pending, no referee) referral instead of stacking duplicates — those
    duplicate rows were the reason the list filled up with un-removable entries."""
    _require_super(user)
    code = await _ensure_ref_code(db, user)
    existing = (await db.execute(
        select(Referral).where(
            Referral.referrer_user_id == user.id,
            Referral.referee_tenant_id.is_(None),
            Referral.status == "pending",
        ).order_by(Referral.created_at.desc())
    )).scalars().first()
    if existing is not None:
        if body.notes is not None:
            existing.notes = body.notes
            await db.commit()
        return {"id": str(existing.id), "share_url": _referral_share_url(code)}
    ref = Referral(referrer_user_id=user.id, status="pending", notes=body.notes)
    db.add(ref)
    await db.commit()
    await db.refresh(ref)
    return {"id": str(ref.id), "share_url": _referral_share_url(code)}

@router.delete("/referrals/{referral_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_referral(
    referral_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Remove a referral row (e.g. a duplicate or test entry)."""
    _require_super(user)
    ref = (await db.execute(
        select(Referral).where(Referral.id == referral_id)
    )).scalars().first()
    if ref is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "referral_not_found")
    await db.delete(ref)
    await db.commit()

# ── settings (singleton key/value) ─────────────────────────────────────────

from app.models import PlatformSetting


@router.get("/settings")
async def list_settings(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_platform(user)
    from app.core.card_networks import card_validation_state
    from app.services.subscription_checkout import BILLING_CARDS_KEY

    rows = (await db.execute(
        select(PlatformSetting).order_by(PlatformSetting.key)
    )).scalars().all()

    def _row(s: PlatformSetting) -> dict:
        out: dict = {"key": s.key, "value": s.value, "description": s.description}
        # Receiving cards carry their resolution state alongside — never
        # inside — the stored value, so the admin UI can show which cards are
        # withheld from the top-up screen and why, and a save round-trip cannot
        # accidentally persist the annotation. Cards predating the `network`
        # field resolve from an exact label or not at all.
        if s.key == BILLING_CARDS_KEY:
            items = s.value.get("items") if isinstance(s.value, dict) else None
            out["validation"] = [
                card_validation_state(c) for c in items if isinstance(c, dict)
            ] if isinstance(items, list) else []
        return out

    return {"items": [_row(s) for s in rows]}

@router.get("/risk/stats")
async def risk_network_stats(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Platform-wide health of the cross-tenant risk network (Round 23)."""
    _require_platform(user)
    from app.models import CustomerRiskSignal, TenantSettings
    from app.models.enums import RiskSignalStatus

    total_signals = (await db.execute(
        select(func.count(CustomerRiskSignal.id))
    )).scalar_one()
    by_status_rows = (await db.execute(
        select(CustomerRiskSignal.status, func.count(CustomerRiskSignal.id))
        .group_by(CustomerRiskSignal.status)
    )).all()
    by_severity_rows = (await db.execute(
        select(CustomerRiskSignal.severity, func.count(CustomerRiskSignal.id))
        .where(CustomerRiskSignal.status == RiskSignalStatus.published)
        .group_by(CustomerRiskSignal.severity)
    )).all()
    opt_in_count = (await db.execute(
        select(func.count(TenantSettings.id)).where(
            TenantSettings.risk_network_opt_in.is_(True)
        )
    )).scalar_one()
    distinct_hashes = (await db.execute(
        select(func.count(func.distinct(CustomerRiskSignal.jshshir_hash)))
        .where(CustomerRiskSignal.status == RiskSignalStatus.published)
    )).scalar_one()
    return {
        "total_signals": int(total_signals),
        "by_status": {st.value: cnt for st, cnt in by_status_rows},
        "published_by_severity": {sev.value: cnt for sev, cnt in by_severity_rows},
        "opt_in_tenant_count": int(opt_in_count),
        "flagged_customers": int(distinct_hashes),
    }
