"""Effective plan limits + overage checks (Round 39).

Resolves a tenant's *effective* plan configuration — the plan row, overlaid with
any per-tenant overrides (``Tenant.plan_overrides``), and the demo-plan config
for trial tenants (which have no plan row). Provides a single ``check_limit``
used at every guarded action so hard/soft behaviour is consistent.

No plan-name hardcoding: behaviour is driven entirely by the limit columns +
the ``overage`` JSON.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan, Tenant, TenantUser

# limit_key → the Plan column holding the included quota (NULL = unlimited).
LIMIT_COLUMN = {
    "contract": "max_active_installments",
    "staff": "max_staff",
    "branch": "max_branches",
    "custom_field": "max_custom_fields",
    "broadcast": "broadcasts_per_month",
}
LIMIT_KEYS = tuple(LIMIT_COLUMN)

# Behaviour messages (uz) surfaced to the tenant.
MSG_HARD = "Sizning tarifingizda bu limit tugagan. Davom etish uchun tarifni yangilang."
MSG_SOFT = "Siz tarif limitidan oshyapsiz. Ushbu qo'shimcha foydalanish oy oxirida hisob-fakturaga qo'shiladi."


@dataclass
class EffectiveConfig:
    plan: Plan | None
    is_trial: bool
    price_monthly: int
    limits: dict[str, int | None]                 # None = unlimited
    overage: dict[str, dict[str, Any]]            # key → {price, enabled, mode}


async def _plan_for(db: AsyncSession, code: str) -> Plan | None:
    return (await db.execute(select(Plan).where(Plan.code == code))).scalar_one_or_none()


async def effective_config(db: AsyncSession, tenant: Tenant) -> EffectiveConfig:
    plan = await _plan_for(db, tenant.plan_code)
    is_trial = plan is None  # trial tenants have no plan row
    overrides = tenant.plan_overrides or {}
    ov_limits = overrides.get("limits") or {}
    ov_overage = overrides.get("overage") or {}

    limits: dict[str, int | None] = {}
    if is_trial:
        from app.services.demo_plan import get_config
        demo = await get_config(db)
        base = {
            "contract": demo.get("max_active_installments"),
            "staff": demo.get("max_staff"),
            "branch": 1,
            "custom_field": 0,
            "broadcast": None,
        }
        for k in LIMIT_KEYS:
            limits[k] = ov_limits.get(k, base.get(k))
        price = int(overrides.get("price_monthly") or 0)
        overage: dict[str, Any] = dict(ov_overage)
    else:
        assert plan is not None  # is_trial is False ⇒ plan row exists
        for k, col in LIMIT_COLUMN.items():
            limits[k] = ov_limits.get(k, getattr(plan, col))
        price = int(overrides.get("price_monthly", int(plan.price_monthly or 0)))
        overage = dict(plan.overage or {})
        overage.update(ov_overage)

    return EffectiveConfig(plan=plan, is_trial=is_trial, price_monthly=price,
                           limits=limits, overage=overage)


@dataclass
class LimitDecision:
    allowed: bool
    over: bool                 # True when the action exceeds the included limit
    mode: str                  # "soft" | "hard"
    overage_price: int         # per-unit overage price (0 if none)
    limit: int | None
    message: str | None


def check(cfg: EffectiveConfig, key: str, current: int, increment: int = 1) -> LimitDecision:
    limit = cfg.limits.get(key)
    ov = cfg.overage.get(key) or {}
    mode = str(ov.get("mode") or "hard")
    price = int(ov.get("price") or 0)
    enabled = bool(ov.get("enabled"))

    if limit is None:  # unlimited
        return LimitDecision(True, False, mode, price, None, None)
    if current + increment <= limit:
        return LimitDecision(True, False, mode, price, limit, None)
    # Would exceed the included quota.
    if enabled and mode == "soft":
        return LimitDecision(True, True, "soft", price, limit, MSG_SOFT)
    return LimitDecision(False, True, "hard", price, limit, MSG_HARD)


async def check_limit(db: AsyncSession, tenant: Tenant, key: str, current: int, increment: int = 1) -> LimitDecision:
    cfg = await effective_config(db, tenant)
    return check(cfg, key, current, increment)


async def locked_staff_decision(db: AsyncSession, tenant_id: UUID, *, increment: int = 1) -> LimitDecision:
    """(#135) Atomically decide whether ``increment`` more staff fit under max_staff.

    Locks the tenant row (``SELECT … FOR UPDATE``) so two concurrent staff adds /
    invite-claims for the same tenant serialize: the second blocks until the first
    commits, then re-counts and sees it. Without the lock both read the same active
    count, both pass ``current + 1 <= limit``, and the cap is exceeded.
    """
    tenant = (await db.execute(
        select(Tenant).where(Tenant.id == tenant_id).with_for_update()
    )).scalar_one_or_none()
    if tenant is None:                      # caller handles a missing tenant elsewhere
        return LimitDecision(True, False, "hard", 0, None, None)
    active = (await db.execute(
        select(func.count(TenantUser.id)).where(
            TenantUser.tenant_id == tenant_id,
            TenantUser.is_active.is_(True),
        )
    )).scalar_one()
    return await check_limit(db, tenant, "staff", int(active), increment)
