"""Platform HQ: broadcasts to Muddato users, and the audience they target."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.phone_identity import account_phone
from app.core.ratelimit import broadcast_limit
from app.core.security import current_user
from app.db import get_db
from app.models import (
    Broadcast,
    Customer,
    Tenant,
    TenantUser,
    User,
)
from app.models.enums import (
    BroadcastStatus,
    BroadcastTarget,
)
from app.schemas.common import LimitQ, OffsetQ

from ._shared import (
    _require_platform,
)

router = APIRouter()

# ── platform-wide broadcasts (#203) ──────────────────────────────────────────
class PlatformBroadcastIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body_uz: str = Field(min_length=1, max_length=4000)
    body_ru: str | None = Field(default=None, max_length=4000)
    media_url: str | None = Field(default=None, max_length=500)
    button_label: str | None = Field(default=None, max_length=64)
    button_url: str | None = Field(default=None, max_length=500)
    # Which slice of the user base to message. Defaults to "owners" — the
    # historical behaviour (an HQ announcement to active shop owners).
    segment: str = Field(default="owners")

def _broadcast_segment(b: Broadcast) -> str:
    tf = b.target_filter or {}
    seg = tf.get("segment") if isinstance(tf, dict) else None
    return seg or "owners"

def _platform_broadcast_dict(b: Broadcast) -> dict:
    return {
        "id": str(b.id),
        "title": b.title,
        "body_uz": b.body_uz,
        "body_ru": b.body_ru,
        "media_url": b.media_url,
        "button_label": b.button_label,
        "button_url": b.button_url,
        "segment": _broadcast_segment(b),
        "status": b.status.value,
        "sent_count": b.sent_count,
        "failed_count": b.failed_count,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }

@router.get("/broadcasts")
async def list_platform_broadcasts(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: OffsetQ = 0,
) -> dict:
    """Recent HQ broadcasts (tenant_id NULL = platform-wide)."""
    _require_platform(user)
    rows = (await db.execute(
        select(Broadcast).where(Broadcast.tenant_id.is_(None))
        .order_by(Broadcast.created_at.desc(), Broadcast.id.desc())
        .offset(offset).limit(100)
    )).scalars().all()
    return {"items": [_platform_broadcast_dict(b) for b in rows], "total": len(rows)}

@router.post("/broadcasts", status_code=status.HTTP_201_CREATED, dependencies=[broadcast_limit()])
async def create_platform_broadcast(
    payload: PlatformBroadcastIn,
    background: BackgroundTasks,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """(#203) Compose an HQ announcement to a chosen user SEGMENT (clients,
    demo-shop owners, …); delivered off the request path via the bot
    (notifications.deliver_platform_broadcast_bg)."""
    _require_platform(user)
    from app.services.user_segments import VALID_SEGMENTS
    if payload.segment not in VALID_SEGMENTS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown_segment")
    b = Broadcast(
        tenant_id=None,
        title=payload.title.strip(),
        body_uz=payload.body_uz.strip(),
        body_ru=(payload.body_ru or None),
        media_url=(payload.media_url or None),
        button_label=payload.button_label or None,
        button_url=payload.button_url or None,
        target=BroadcastTarget.all if payload.segment == "all" else BroadcastTarget.custom,
        target_filter={"segment": payload.segment},
        status=BroadcastStatus.sending,
        created_by_user_id=user.id,
        started_at=datetime.now(UTC),
    )
    db.add(b)
    await db.flush()
    await db.commit()
    await db.refresh(b)
    from app.services.notifications import deliver_platform_broadcast_bg
    background.add_task(deliver_platform_broadcast_bg, broadcast_id=b.id)
    return _platform_broadcast_dict(b)

# ── all-users directory + segmentation (advanced broadcasting) ────────────────
@router.get("/users/segments")
async def list_user_segments(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Distinct Telegram-recipient counts per segment — the broadcast composer's
    reach preview. Platform-admin only."""
    _require_platform(user)
    from app.services.user_segments import SEGMENTS, segment_counts
    counts = await segment_counts(db)
    return {"segments": [
        {"key": k, "label_uz": SEGMENTS[k][0], "label_ru": SEGMENTS[k][1], "count": counts.get(k, 0)}
        for k in SEGMENTS
    ]}

@router.get("/users")
async def list_platform_users(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    segment: str = "all",
    q: str | None = None,
    limit: LimitQ = 50,
    offset: OffsetQ = 0,
) -> dict:
    """Every Muddato user (a Telegram recipient), filterable by segment +
    name/phone/username search, with their shop memberships and client flag.
    Platform-admin only (PII — phones)."""
    _require_platform(user)
    from app.services.user_segments import VALID_SEGMENTS, users_query
    if segment not in VALID_SEGMENTS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown_segment")

    stmt = users_query(segment, q)
    total = (await db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )).scalar_one()
    rows = (await db.execute(
        stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)
    )).scalars().all()

    # Batch the two enrichments for the page (no N+1): client flag + memberships.
    tg_ids = [u.telegram_id for u in rows if u.telegram_id is not None]
    client_tg: set[int] = set()
    if tg_ids:
        client_tg = {
            tid for tid in (await db.execute(
                select(Customer.telegram_id).where(Customer.telegram_id.in_(tg_ids)).distinct()
            )).scalars().all() if tid is not None
        }
    user_ids = [u.id for u in rows]
    memb: dict[UUID, list[dict]] = {}
    if user_ids:
        for uid, role, tname, tstatus in (await db.execute(
            select(TenantUser.user_id, TenantUser.role, Tenant.name, Tenant.status)
            .join(Tenant, Tenant.id == TenantUser.tenant_id)
            .where(TenantUser.user_id.in_(user_ids), TenantUser.is_active.is_(True))
        )).all():
            memb.setdefault(uid, []).append(
                {"tenant": tname, "role": role.value, "status": tstatus.value}
            )

    items = [{
        "id": str(u.id),
        "telegram_id": u.telegram_id,
        "telegram_username": u.telegram_username,
        "first_name": u.first_name,
        "last_name": u.last_name,
        # The canonical account phone, and — separately and by its own name —
        # the legacy typed contact detail some older rows still carry. One field
        # holding "whichever of the two exists" is what made the directory a
        # third opinion about a person's number; two named fields make the
        # operator's screen say which is which.
        "phone": account_phone(u),
        "legacy_contact_phone": u.phone,
        "locale": u.locale,
        "platform_role": u.platform_role.value if u.platform_role else None,
        "is_client": u.telegram_id in client_tg,
        "memberships": memb.get(u.id, []),
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
    } for u in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
