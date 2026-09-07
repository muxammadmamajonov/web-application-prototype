"""Platform HQ: read-only listings of tenants, plans and admins, the admin role
edits that act on that listing, and the audit log."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import current_user
from app.db import get_db
from app.models import (
    Plan,
    Tenant,
    User,
)
from app.models.enums import (
    PlatformRole,
    TenantStatus,
)
from app.schemas.common import LimitQ, OffsetQ

from ._shared import (
    AUDIT_ACTION_SCAN_LIMIT,
    _require_platform,
    _require_super,
)

router = APIRouter()

# ── tenant list (unchanged below) ──────────────────────────────────────────

@router.get("/tenants")
async def list_tenants(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = None,
    limit: LimitQ = 50,
    offset: OffsetQ = 0,
) -> dict:
    _require_platform(user)
    q = select(Tenant).order_by(Tenant.created_at.desc())
    if status_filter:
        try:
            q = q.where(Tenant.status == TenantStatus(status_filter))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_status") from None
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    # (#139) Count the FILTERED query so the total matches the status filter.
    total = (await db.execute(
        select(func.count()).select_from(q.order_by(None).subquery())
    )).scalar_one()
    return {
        "items": [
            {
                "id": str(t.id),
                "slug": t.slug,
                "name": t.name,
                "status": t.status.value,
                "plan_code": t.plan_code,
                "locale": t.locale,
                "contact_phone": t.contact_phone,
                # Owner links are single-use tokens minted on demand, never a
                # guessable slug — the drawer fetches a fresh one when needed.
                "share_url": None,
                "created_at": t.created_at.isoformat(),
            }
            for t in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }

@router.get("/plans")
async def list_plans(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_platform(user)
    rows = (await db.execute(select(Plan).order_by(Plan.sort_order))).scalars().all()
    return {
        "items": [
            {
                "id": str(p.id),
                "code": p.code,
                "name_uz": p.name_uz, "name_ru": p.name_ru,
                "description_uz": p.description_uz, "description_ru": p.description_ru,
                "price_monthly": int(p.price_monthly or 0),
                "max_active_installments": p.max_active_installments,
                "features": p.features,
                "is_active": p.is_active,
                "bot_mode": p.bot_mode,
                "allow_custom_domain": p.allow_custom_domain,
                "max_custom_fields": p.max_custom_fields,
                "max_staff": p.max_staff,
                "broadcasts_per_month": p.broadcasts_per_month,
                "allow_pdf_template_upload": p.allow_pdf_template_upload,
                "allow_api_keys": p.allow_api_keys,
                "allow_outbound_webhooks": p.allow_outbound_webhooks,
                "sms_included_per_month": p.sms_included_per_month,
                "commission_management": p.commission_management,
                "multi_branch": p.multi_branch,
                "risk_network_access": p.risk_network_access,
                "reports_advanced": p.reports_advanced,
                "badge": p.badge,
                "price_per_installment": int(p.price_per_installment) if p.price_per_installment is not None else None,
                "sort_order": p.sort_order,
                "max_branches": p.max_branches,
                "overage": p.overage or {},
                "is_popular": p.is_popular,
                "is_public": p.is_public,
                "is_contact_sales": p.is_contact_sales,
            }
            for p in rows
        ],
    }

# ── admins ─────────────────────────────────────────────────────────────────

@router.get("/admins")
async def list_admins(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_platform(user)
    rows = (await db.execute(
        select(User).where(User.platform_role.is_not(None)).order_by(User.created_at.desc())
    )).scalars().all()
    return {
        "items": [
            {
                "id": str(u.id),
                "telegram_id": u.telegram_id,
                "telegram_username": u.telegram_username,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "photo_url": u.photo_url,
                "platform_role": u.platform_role.value if u.platform_role else None,
                "is_active": u.is_active,
                "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
                "created_at": u.created_at.isoformat(),
            }
            for u in rows
        ],
    }

class AdminUpdateIn(BaseModel):
    role: str | None = Field(default=None, pattern="^(super_admin|support)$")
    is_active: bool | None = None

@router.patch("/admins/{user_id}")
async def update_admin(
    user_id: str,
    body: AdminUpdateIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Change a platform admin's role or active flag. Super-admin only; you
    cannot demote or deactivate yourself (prevents locking the platform out)."""
    _require_super(user)
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None or target.platform_role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "admin_not_found")
    is_self = str(target.id) == str(user.id)
    if is_self and (body.is_active is False or (body.role is not None and body.role != PlatformRole.super_admin.value)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot_demote_self")
    if body.role is not None:
        target.platform_role = PlatformRole(body.role)
    if body.is_active is not None:
        target.is_active = body.is_active
    await db.commit()
    await db.refresh(target)
    from app.services import audit
    await audit.record(db, action="admin.update", actor_user_id=user.id,
                       entity_type="user", entity_id=str(target.id))
    return {"id": str(target.id), "platform_role": target.platform_role.value if target.platform_role else None,
            "is_active": target.is_active}

@router.delete("/admins/{user_id}", status_code=status.HTTP_200_OK)
async def remove_admin(
    user_id: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Revoke platform access (strips platform_role; the user record stays, since
    they may also be a tenant member). Super-admin only; cannot remove yourself."""
    _require_super(user)
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target is None or target.platform_role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "admin_not_found")
    if str(target.id) == str(user.id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot_remove_self")
    target.platform_role = None
    await db.commit()
    from app.services import audit
    await audit.record(db, action="admin.revoke", actor_user_id=user.id,
                       entity_type="user", entity_id=str(target.id))
    return {"removed": str(target.id)}

# ── audit log ──────────────────────────────────────────────────────────────

from app.models import AuditLog


@router.get("/audit")
async def list_audit(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: LimitQ = 50, offset: OffsetQ = 0,
    action: str | None = None,        # exact action OR a "tenant." category prefix
    tenant_id: str | None = None,
    date_from: str | None = None,     # ISO date (inclusive)
    date_to: str | None = None,       # ISO date (inclusive)
) -> dict:
    _require_platform(user)
    q = select(AuditLog)
    cnt = select(func.count(AuditLog.id))
    # ``action`` and ``tenant_id`` accept comma-separated lists (multi-select
    # filters); any action token ending with "." is a category prefix.
    if action:
        tokens = [a for a in (s.strip() for s in action.split(",")) if a]
        if tokens:
            from sqlalchemy import or_ as _or
            clauses = [
                AuditLog.action.like(f"{tok}%") if tok.endswith(".") else AuditLog.action == tok
                for tok in tokens
            ]
            q = q.where(_or(*clauses))
            cnt = cnt.where(_or(*clauses))
    if tenant_id:
        try:
            tids = [UUID(s.strip()) for s in tenant_id.split(",") if s.strip()]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_tenant_id") from None
        if tids:
            q = q.where(AuditLog.tenant_id.in_(tids))
            cnt = cnt.where(AuditLog.tenant_id.in_(tids))
    if date_from:
        from datetime import date as _date
        try:
            df = datetime.combine(_date.fromisoformat(date_from), datetime.min.time(), tzinfo=UTC)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_date") from None
        q = q.where(AuditLog.created_at >= df)
        cnt = cnt.where(AuditLog.created_at >= df)
    if date_to:
        from datetime import date as _date
        try:
            dt = datetime.combine(_date.fromisoformat(date_to), datetime.max.time(), tzinfo=UTC)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_date") from None
        q = q.where(AuditLog.created_at <= dt)
        cnt = cnt.where(AuditLog.created_at <= dt)

    rows = (await db.execute(
        q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all()
    total = (await db.execute(cnt)).scalar_one()
    # Actions present, for the filter dropdown.
    #
    # MUST stay bounded: `audit_log` is the largest, fastest-growing table in
    # the product and this runs on every load of the audit screen. The action
    # vocabulary is application-authored and small, so the recent window covers
    # it in practice while keeping the planner on the `created_at DESC` index the
    # page already uses.
    recent = (
        select(AuditLog.action)
        .order_by(AuditLog.created_at.desc())
        .limit(AUDIT_ACTION_SCAN_LIMIT)
        .subquery()
    )
    actions = sorted({r[0] for r in (await db.execute(select(recent.c.action).distinct())).all() if r[0]})

    # Who actually did it. The rows carry only `actor_user_id`, so the audit
    # screen could show nothing but a truncated UUID ("ffd713f7-d60") — for a log
    # whose entire purpose is answering "who did this", that is unreadable, and
    # an operator reviewing an incident had to look every id up by hand.
    #
    # Resolved in ONE query over the ids on the page (never per row), and only
    # for actors that still exist: a name is reported when the database can
    # prove it, and is absent otherwise. The client already renders a missing
    # name as absent rather than inventing a placeholder, so a deleted user
    # degrades to the id instead of to a wrong person.
    actor_ids = {a.actor_user_id for a in rows if a.actor_user_id}
    actor_names: dict[UUID, str] = {}
    if actor_ids:
        actor_names = {
            uid: (" ".join(p for p in (first, last) if p).strip() or (f"@{uname}" if uname else ""))
            for uid, first, last, uname in (
                await db.execute(
                    select(User.id, User.first_name, User.last_name, User.telegram_username)
                    .where(User.id.in_(actor_ids))
                )
            ).all()
        }

    return {
        "actions": actions,
        "items": [
            {
                "id": str(a.id),
                "tenant_id": str(a.tenant_id) if a.tenant_id else None,
                "actor_user_id": str(a.actor_user_id) if a.actor_user_id else None,
                # `actor_user_id` is nullable — system-generated entries have no
                # human behind them — so the id is checked before it is used as
                # a key rather than looking up None and relying on a miss.
                "actor_name": (actor_names.get(a.actor_user_id) or None) if a.actor_user_id else None,
                "action": a.action,
                "entity_type": a.entity_type,
                "entity_id": a.entity_id,
                "ip": a.ip,
                "created_at": a.created_at.isoformat(),
            }
            for a in rows
        ],
        "total": total,
    }
