"""Platform HQ: the write side of commercial configuration — a tenant's plan,
overrides and status, admin invitations, plan definitions and platform settings."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import invite_codes
from app.core.ratelimit import invite_limit
from app.core.security import current_user
from app.db import get_db
from app.models import (
    Plan,
    Tenant,
    User,
)
from app.models.enums import TenantStatus

from ._shared import (
    _require_super,
)

router = APIRouter()

class SetPlanIn(BaseModel):
    plan_code: str = Field(min_length=1, max_length=32)

@router.post("/tenants/{tenant_id}/set-plan")
async def set_tenant_plan(
    tenant_id: str,
    body: SetPlanIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Platform override — set a tenant's plan directly (no balance charge)."""
    _require_super(user)
    try:
        tid = UUID(tenant_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_tenant_id") from None
    # The target plan must exist (or be the built-in "trial").
    if body.plan_code != "trial":
        exists = (await db.execute(
            select(Plan.id).where(Plan.code == body.plan_code)
        )).first()
        if not exists:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown_plan")
    t = (await db.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant_not_found")
    prev = t.plan_code
    t.plan_code = body.plan_code
    if body.plan_code != "trial" and t.status in (TenantStatus.demo, TenantStatus.suspended):
        t.status = TenantStatus.active
        t.trial_ends_at = None
        t.suspended_at = None
    from app.services import audit
    await audit.record(
        db, action="tenant.set_plan", tenant_id=t.id, actor_user_id=user.id,
        entity_type="tenant", entity_id=str(t.id), after={"from": prev, "to": body.plan_code},
    )
    await db.commit()
    return {"plan_code": t.plan_code, "status": t.status.value}

def _reject_negative_overage(v: dict | None) -> dict | None:
    """(#134) Reject negative per-unit prices in an overage map {key: {price, ...}};
    a negative overage price flows into compute_amount and yields a negative line."""
    if v:
        for key, spec in v.items():
            if not isinstance(spec, dict) or spec.get("price") is None:
                continue
            try:
                price = float(spec["price"])
            except (TypeError, ValueError) as e:
                raise ValueError(f"overage.{key}.price must be a number") from e
            if price < 0:
                raise ValueError(f"overage.{key}.price must be >= 0")
    return v

def _reject_negative_limits(v: dict | None) -> dict | None:
    """(#134) Reject negative caps in a limits map {key: int|null}."""
    if v:
        for key, val in v.items():
            if val is None:
                continue
            try:
                n = int(val)
            except (TypeError, ValueError) as e:
                raise ValueError(f"limit.{key} must be an integer") from e
            if n < 0:
                raise ValueError(f"limit.{key} must be >= 0")
    return v

class PlanOverridesIn(BaseModel):
    # Any subset; null clears that override.
    price_monthly: int | None = Field(default=None, ge=0)
    limits:        dict | None = None     # {limit_key: int|null}
    overage:       dict | None = None     # {limit_key: {price, enabled, mode}}

    @field_validator("overage")
    @classmethod
    def _ck_overage(cls, v: dict | None) -> dict | None:
        return _reject_negative_overage(v)

    @field_validator("limits")
    @classmethod
    def _ck_limits(cls, v: dict | None) -> dict | None:
        return _reject_negative_limits(v)

@router.put("/tenants/{tenant_id}/plan-overrides")
async def set_plan_overrides(
    tenant_id: str,
    body: PlanOverridesIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Per-customer plan overrides — custom subscription price, limits, and
    overage prices for one business (Round 39)."""
    _require_super(user)
    try:
        tid = UUID(tenant_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_tenant_id") from None
    t = (await db.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant_not_found")
    ov: dict = {}
    if body.price_monthly is not None:
        ov["price_monthly"] = body.price_monthly
    if body.limits is not None:
        ov["limits"] = body.limits
    if body.overage is not None:
        ov["overage"] = body.overage
    t.plan_overrides = ov
    await db.commit()
    return {"plan_overrides": t.plan_overrides}

class TenantStatusIn(BaseModel):
    status: str = Field(pattern="^(active|demo|suspended|blocked)$")

@router.patch("/tenants/{tenant_id}/status")
async def set_tenant_status(
    tenant_id: str,
    body: TenantStatusIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    # (LOW-1) Parse the path segment before it reaches the query, exactly as the
    # neighbouring `set_plan_overrides` does: a non-UUID string reached asyncpg
    # and surfaced as a 500 for what is a malformed request.
    try:
        tid = UUID(tenant_id)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_tenant_id") from None
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant_not_found")
    prev = tenant.status.value
    tenant.status = TenantStatus(body.status)
    from app.services import audit
    await audit.record(
        db, action="tenant.status_change", tenant_id=tenant.id,
        actor_user_id=user.id, entity_type="tenant", entity_id=str(tenant.id),
        before={"status": prev}, after={"status": tenant.status.value},
    )
    await db.commit()
    return {"id": str(tenant.id), "status": tenant.status.value}

# ── admin invite ───────────────────────────────────────────────────────────

class AdminInviteLinkIn(BaseModel):
    role: str = Field(default="support", pattern="^(super_admin|support)$")
    expires_hours: int = Field(default=72, ge=1, le=720)

@router.post("/admin-invitations", status_code=status.HTTP_201_CREATED, dependencies=[invite_limit()])
async def create_admin_invitation(
    body: AdminInviteLinkIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Mint a single-use, role-scoped link that promotes whoever claims it.

    Security: only an existing platform admin can mint it, the role is baked
    into the token row (not chosen by the claimer), it is single-use and
    expires. The opener logs in via Telegram and is promoted on claim — no
    open ``anyone-becomes-admin`` URL.
    """
    _require_super(user)
    from datetime import timedelta

    from app.config import settings
    from app.models import Invitation

    raw_token, token_verifier = await invite_codes.mint(db)
    inv = Invitation(
        token_verifier=token_verifier,
        tenant_id=None,
        kind="platform_admin",
        role=body.role,
        preset={},
        created_by_user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=body.expires_hours),
    )
    db.add(inv)
    await db.flush()
    from app.services import audit
    await audit.record(
        db, action="admin_invitation.create",
        actor_user_id=user.id, entity_type="invitation", entity_id=str(inv.id),
        after={"role": body.role},
    )
    await db.commit()
    await db.refresh(inv)

    return {
        "id": str(inv.id),
        "token": raw_token,
        "role": inv.role,
        "telegram_url": invite_codes.deep_link(settings.telegram_bot_username, raw_token),
        "web_url": invite_codes.web_url(raw_token),
        "expires_at": inv.expires_at.isoformat(),
    }

# ── plan edit ──────────────────────────────────────────────────────────────

from decimal import Decimal as _Decimal


class PlanPatchIn(BaseModel):
    price_monthly:           _Decimal | None = Field(default=None, ge=0)
    max_active_installments: int | None = Field(default=None, ge=0)
    is_active:               bool | None = None
    name_uz:                 str | None = None
    name_ru:                 str | None = None
    description_uz:          str | None = None
    description_ru:          str | None = None
    # New plan capability flags (admin can rebalance plans without code change)
    bot_mode:                Literal["shared", "tenant"] | None = None
    allow_custom_domain:     bool | None = None
    max_custom_fields:       int | None = Field(default=None, ge=0)
    max_staff:               int | None = Field(default=None, ge=0)
    broadcasts_per_month:    int | None = Field(default=None, ge=0)
    # Round 26 capability flags + marketing badge + custom-plan unit price
    allow_pdf_template_upload: bool | None = None
    allow_api_keys:            bool | None = None
    allow_outbound_webhooks:   bool | None = None
    sms_included_per_month:    int | None = Field(default=None, ge=0)
    commission_management:     bool | None = None
    multi_branch:              bool | None = None
    risk_network_access:       bool | None = None
    reports_advanced:          bool | None = None
    badge:                     Literal["limited", "most_popular", "full", "custom"] | None = None
    price_per_installment:     _Decimal | None = Field(default=None, ge=0)
    sort_order:                int | None = Field(default=None, ge=0)
    # Round 39 — subscription + overage model
    max_branches:              int | None = Field(default=None, ge=0)
    overage:                   dict | None = None
    features:                  dict | None = None
    is_popular:                bool | None = None
    is_public:                 bool | None = None
    is_contact_sales:          bool | None = None

    @field_validator("overage")
    @classmethod
    def _ck_overage(cls, v: dict | None) -> dict | None:
        return _reject_negative_overage(v)

@router.patch("/plans/{code}")
async def update_plan(
    code: str,
    body: PlanPatchIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    plan = (await db.execute(select(Plan).where(Plan.code == code))).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan_not_found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(plan, k, v)
    from app.services import audit
    await audit.record(
        db, action="plan.update", actor_user_id=user.id,
        entity_type="plan", entity_id=plan.code,
        after={k: (float(v) if hasattr(v, "is_finite") else v) for k, v in changes.items()},
    )
    await db.commit()
    return {"code": plan.code, "price_monthly": int(plan.price_monthly or 0)}

# How many plans the platform owner may create. "trial" is the built-in tier
# (not stored as a row), so this caps the manageable, owner-defined plans.
MAX_PLANS = 5

class PlanCreateIn(BaseModel):
    # Free-form slug — lowercase letters/digits/hyphen, owner's choice.
    code: str = Field(pattern="^[a-z0-9][a-z0-9-]{0,31}$")
    name_uz: str = Field(min_length=1, max_length=80)
    name_ru: str = Field(min_length=1, max_length=80)
    price_monthly: _Decimal = Field(default=_Decimal("0"), ge=0)
    max_active_installments: int | None = Field(default=None, ge=0)
    description_uz: str | None = None
    description_ru: str | None = None
    is_active: bool = True

@router.post("/plans", status_code=status.HTTP_201_CREATED)
async def create_plan(
    body: PlanCreateIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create an owner-defined plan. Codes are free-form (any slug) but the
    total number of stored plans is capped at ``MAX_PLANS``. ``trial`` is a
    reserved built-in tier and cannot be created as a row."""
    _require_super(user)
    if body.code == "trial":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "reserved_code")
    existing = (await db.execute(select(Plan).where(Plan.code == body.code))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "plan_exists")
    count = (await db.execute(select(func.count(Plan.id)))).scalar_one()
    if count >= MAX_PLANS:
        # Distinct from the tenant-side ``plan_limit_reached`` (402, structured
        # detail), which means a SHOP hit the contract ceiling of the plan it
        # subscribes to. This one is the platform's own catalogue ceiling: a
        # different actor with a different remedy, so a different code.
        raise HTTPException(status.HTTP_409_CONFLICT, "platform_plan_limit_reached")
    plan = Plan(
        code=body.code,
        name_uz=body.name_uz, name_ru=body.name_ru,
        description_uz=body.description_uz, description_ru=body.description_ru,
        price_monthly=body.price_monthly,
        max_active_installments=body.max_active_installments,
        is_active=body.is_active,
    )
    db.add(plan)
    from app.services import audit
    await audit.record(
        db, action="plan.create", actor_user_id=user.id,
        entity_type="plan", entity_id=body.code,
    )
    await db.commit()
    await db.refresh(plan)
    return {"id": str(plan.id), "code": plan.code}

@router.delete("/plans/{code}", status_code=status.HTTP_200_OK)
async def delete_plan(
    code: str,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    plan = (await db.execute(select(Plan).where(Plan.code == code))).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan_not_found")
    in_use = (await db.execute(
        select(func.count(Tenant.id)).where(Tenant.plan_code == code)
    )).scalar_one()
    if in_use:
        raise HTTPException(status.HTTP_409_CONFLICT, "plan_in_use")
    from app.services import audit
    await audit.record(
        db, action="plan.delete", actor_user_id=user.id,
        entity_type="plan", entity_id=code,
    )
    await db.delete(plan)
    await db.commit()
    return {"deleted": code}

# ── platform setting upsert ────────────────────────────────────────────────

class SettingIn(BaseModel):
    value: dict | list | str | int | bool | None
    description: str | None = None


_BILLING_CARDS_KEY = "billing_cards"
_DEFAULTS_KEY = "defaults"
_YEARLY_KEY = "yearly_billing"


def _validated_yearly(value: dict) -> dict:
    """Refuse a yearly discount that is not a real percentage.

    This number is multiplied against every published price, so a typo does not
    produce a wrong screen — it produces a wrong PRICE, on the landing page,
    until somebody notices. `20` typed as `200` would make every plan free and
    then negative. Checked here, on the way in, rather than clamped silently on
    the way out: an operator who meant 200 needs to be told, not quietly given 90.
    """
    from app.services.yearly_billing import MAX_DISCOUNT_PCT

    # Arbitrary JSON off the wire — including absent. `int()` rejects both the
    # wrong type and None, and the handler below reports either as one 400.
    raw: Any = value.get("discount_pct")
    try:
        pct = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"error": "yearly_discount_invalid", "value": raw},
        ) from None
    if not (0 <= pct <= MAX_DISCOUNT_PCT):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"error": "yearly_discount_out_of_range",
                    "value": pct, "max": MAX_DISCOUNT_PCT},
        )
    return {"enabled": bool(value.get("enabled", True)), "discount_pct": pct}


async def _validated_defaults(db: AsyncSession, value: dict) -> dict:
    """Refuse to store a `default_plan` the plan catalogue does not contain.

    Plan codes became owner-chosen slugs in R36 and the stored default was left
    pointing at `lite` long after that code disappeared — a dangling default
    nothing rejected and nothing reported, which every shop provisioned since was
    stamped with. An unchecked write is what let it persist, so the check lives
    on the write.

    Clearing the field (empty string / null) stays allowed: "no platform default,
    use the catalogue's first active plan" is a legitimate configuration.
    """
    from app.services.platform_defaults import unknown_plan_code

    code = value.get("default_plan")
    if isinstance(code, str) and await unknown_plan_code(db, code.strip()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"error": "default_plan_unknown", "code": code},
        )
    return value


def _validated_billing_cards(value: dict) -> dict:
    """Refuse to store a receiving card whose network cannot be resolved.

    Such a card is withheld from every top-up currency, so storing one would
    leave a card that exists in settings and appears nowhere. The same save
    writes the resolved `network` + `settlement_currency` back, so a card that
    resolved from a legacy label stops depending on it.

    The error carries ids and last-four digits only — it reaches a browser and
    the platform's logs.
    """
    from app.core.card_networks import card_validation_state, normalized_card

    items = value.get("items")
    if not isinstance(items, list):
        return value

    states = [card_validation_state(c) for c in items if isinstance(c, dict)]
    unresolved = [st for st in states if not st["resolved"]]
    if unresolved:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "billing_card_network_required",
                "cards": [
                    {"id": st["id"], "last4": st["last4"], "reason": st["reason"]}
                    for st in unresolved
                ],
            },
        )
    return {
        **value,
        "items": [normalized_card(c) if isinstance(c, dict) else c for c in items],
    }

@router.put("/settings/{key}")
async def put_setting(
    key: str,
    body: SettingIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    _require_super(user)
    # `PlatformSetting` is imported lazily below; declare it here too.
    from app.models import PlatformSetting as _PS
    s = (await db.execute(select(_PS).where(_PS.key == key))).scalar_one_or_none()
    value_dict = body.value if isinstance(body.value, dict) else {"v": body.value}
    if key == _BILLING_CARDS_KEY:
        value_dict = _validated_billing_cards(value_dict)
    if key == _DEFAULTS_KEY:
        value_dict = await _validated_defaults(db, value_dict)
    if key == _YEARLY_KEY:
        value_dict = _validated_yearly(value_dict)
    if s is None:
        s = _PS(key=key, value=value_dict, description=body.description)
        db.add(s)
    else:
        s.value = value_dict
        if body.description is not None:
            s.description = body.description
    await db.commit()
    return {"key": s.key, "value": s.value}
