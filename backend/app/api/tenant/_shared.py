"""Cross-domain pieces of the tenant API: request scoping, role guards and the
value bounds every write path shares.

Domain modules import from here; nothing here imports a domain module, so the
package has no internal cycles.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import (
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import current_user
from app.core.validators import UzPhone, UzPhoneOpt
from app.db import get_db, set_tenant_scope
from app.models import (
    Tenant,
    TenantUser,
    User,
)
from app.models.enums import (
    TenantRole,
)
from app.services.tenant_status import (
    MUTATING_METHODS,
    may_mutate,
    route_path,
)

log = logging.getLogger("muddato.tenant")

# ── tenant resolution ──────────────────────────────────────────────────────

def _bind_tenant_log(tenant_id: UUID) -> None:
    """(#145) Bind tenant_id into the structlog request context so every log line
    during a tenant-scoped request is attributable to the tenant (the request
    middleware binds only request_id/path/method). Defensive — never raises."""
    try:
        import structlog
        structlog.contextvars.bind_contextvars(tenant_id=str(tenant_id))
    except Exception:
        # Losing tenant attribution degrades log correlation but must never fail the
        # request. Debug: fires per tenant-scoped request when structlog is absent.
        log.debug("tenant.log_context.bind_failed tenant=%s", tenant_id, exc_info=True)

# (#736) State-changing methods — an impersonated request using one of these must
# always leave an audit trace (see resolve_tenant below). Re-exported from
# app.services.tenant_status, which is also where the locked-tenant write rule
# that consumes it lives; one definition, so the two can never drift.
_MUTATING_METHODS = MUTATING_METHODS

async def resolve_tenant(
    request: Request,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> tuple[Tenant, TenantUser]:
    """Return ``(tenant, membership)`` for the user; 404 if no access."""
    from app.services import audit
    audit.set_impersonator(None)  # (#127) default; only an impersonated request stamps it
    q = select(TenantUser).where(TenantUser.user_id == user.id, TenantUser.is_active.is_(True))
    if x_tenant_id:
        try:
            q = q.where(TenantUser.tenant_id == UUID(x_tenant_id))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_tenant_id") from None
    membership = (await db.execute(q.limit(1))).scalar_one_or_none()
    if membership is None:
        # Platform super-admins can enter any tenant's dashboard (support /
        # fixing issues). We synthesize an owner-level context — not persisted —
        # but require an explicit X-Tenant-Id so they can't act on a tenant by
        # accident. Audit-logged so impersonation is traceable.
        is_super = getattr(user.platform_role, "value", None) == "super_admin"
        if is_super and x_tenant_id:
            try:
                tid = UUID(x_tenant_id)
            except ValueError:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_tenant_id") from None
            # Pin the scope BEFORE the lookup, not after it.
            #
            # THE LOOP THIS FIXES (production, 2026-08-14): "boshqaruv panelga
            # kirishda loop bilan dashboardga qaytib qolmoqda". Under
            # `rf0failclosed` the `muddato_app` policy on `tenants` is
            #
            #     id = app_current_tenant()  OR  EXISTS (an active membership)
            #
            # and this dependency ran its lookup with the GUC still blank,
            # because setting the scope was the LAST thing this branch did. A
            # super-admin deliberately has no membership in the shop they are
            # entering, so both arms were false and the row was invisible — the
            # one branch whose whole purpose is reaching a tenant the caller is
            # not a member of was the branch RLS hid every tenant from.
            #
            # It surfaced as an endless bounce rather than an error: the 404 is
            # `tenant_not_found`, which the client reads as "this shop is gone",
            # so it dropped the impersonation and reloaded to the operator's own
            # home — where pressing "enter" started the same round again.
            #
            # Setting the scope first satisfies the FIRST arm of the policy, so
            # the row is visible without widening anything: the reach is exactly
            # the one tenant named in `X-Tenant-Id`, which is the scope this
            # request was always going to run under. `is_super` above is what
            # authorizes being here at all, and the row must still exist.
            await set_tenant_scope(db, tid)
            tenant = (
                await db.execute(select(Tenant).where(Tenant.id == tid))
            ).scalar_one_or_none()
            if tenant is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant_not_found")
            # (#109) Leave a trace EVERY time a super-admin enters a tenant they
            # are not a member of. The comment above used to claim this was
            # audit-logged; it wasn't. Commit the audit row here (this runs in a
            # dependency, so a read-only handler would otherwise roll it back),
            # then mark the synthesized membership so downstream handlers can
            # flag impersonated mutations.
            # (#128) Dedup: audit the ENTER once per ~hour per (admin, tenant), not
            # on every request — it previously wrote a row + committed each request.
            # Redis SET NX EX is truthy only when newly set; a Redis outage fails
            # OPEN (still audit) so the impersonation trace is never lost.
            should_audit = True
            try:
                from app.core.redis import get_redis
                key = f"superadmin_enter:{user.id}:{tenant.id}"
                should_audit = bool(await get_redis().set(key, "1", nx=True, ex=3600))
            except Exception:
                should_audit = True
            if should_audit:
                await audit.record(
                    db, action="tenant.super_admin_enter",
                    tenant_id=tenant.id, actor_user_id=user.id,
                    entity_type="tenant", entity_id=str(tenant.id),
                    after={"impersonating": True},
                )
                await db.commit()
            # (#127) Stamp the impersonator for the rest of the request so every
            # mutation's audit row is attributed to the entering super-admin.
            audit.set_impersonator(user.id)
            # (#736) The per-enter row above is deduped (~1/hr), so it can't attribute
            # individual writes. Guarantee a trace for EVERY impersonated MUTATION —
            # even one made via a handler that doesn't call audit.record (or records a
            # non-allowlisted action) — by logging the impersonated mutating request
            # itself, committed now so a later handler rollback can't erase it.
            # record() stamps _impersonated_by from the contextvar just set above.
            if request.method in _MUTATING_METHODS:
                await audit.record(
                    db, action="tenant.impersonated_mutation",
                    tenant_id=tenant.id, actor_user_id=user.id,
                    entity_type="tenant", entity_id=str(tenant.id),
                    after={"method": request.method, "path": request.url.path},
                )
                await db.commit()
            membership = TenantUser(
                tenant_id=tenant.id, user_id=user.id, role=TenantRole.owner, is_active=True,
            )
            membership.impersonated = True  # type: ignore[attr-defined]  # transient flag, not persisted
            # (#95) Scope DB work to the impersonated tenant under RLS too — a
            # super-admin entering one tenant should see only that tenant's rows.
            await set_tenant_scope(db, tenant.id)
            _bind_tenant_log(tenant.id)
            # (#MUDD-54 R4) DELIBERATE: this return is above the locked-tenant
            # write guard below, so a platform super-admin can still mutate a
            # `blocked`/`suspended` shop. That is the point of the branch — the
            # admin who has to unpick a wrongly-locked shop, or act on one at the
            # owner's request, cannot be refused by the same lock they are there
            # to lift. It is a bypass, not an oversight, and it is not silent:
            # every mutating request through here has just written (and
            # committed) a `tenant.impersonated_mutation` audit row above, and
            # `audit.set_impersonator` stamps the admin onto every audit row the
            # handler writes afterwards. Moving this return below the guard would
            # leave a locked shop with no operator remedy at all.
            return tenant, membership
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no_tenant_membership")
    tenant = (await db.execute(select(Tenant).where(Tenant.id == membership.tenant_id))).scalar_one()
    # (#95) Pin the RLS scope for the rest of the request now that the tenant is
    # known: every subsequent query runs as muddato_app with app.tenant_id set.
    await set_tenant_scope(db, tenant.id)
    _bind_tenant_log(tenant.id)

    # (#MUDD-25 H1) Fail-closed guard: block mutations on blocked/suspended tenants.
    # GET operations (reading) are allowed; state-changing operations are forbidden.
    #
    # (#MUDD-54 R1) ...except on the two routes a locked shop needs in order to
    # STOP being locked — paying in and choosing a plan. Refusing those too made
    # the lock permanent: every expired trial and every overdue shop needed a
    # platform admin to edit the row by hand, so the conversion funnel and the
    # debt-recovery funnel were both closed. The rule, the reasoning and the
    # exact-route allowlist live in app/services/tenant_status.py; the decision
    # stays HERE, refusing by default, so a route added later is locked until
    # somebody deliberately allowlists it.
    if not may_mutate(tenant, method=request.method, path=route_path(request)):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "tenant_blocked_or_suspended"
        )

    return tenant, membership

#: Used when a tenant's stored timezone is missing or not a real zone. Muddato's
#: market is Uzbekistan, and this matches the Tenant model's own column default,
#: so the fallback keeps a broken row behaving like a normal one rather than
#: silently switching the shop to UTC and shifting its whole working day.
DEFAULT_TENANT_TZ = "Asia/Tashkent"

def _tenant_tz_name(tenant: Tenant) -> str:
    """The tenant's timezone name, or the default when it is unusable."""
    name = (getattr(tenant, "timezone", None) or "").strip()
    if not name:
        return DEFAULT_TENANT_TZ
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # A tenant row can carry a stale or mistyped zone (it is a free string
        # column). That must degrade to the default, never 500 a work queue.
        return DEFAULT_TENANT_TZ
    return name

def _tenant_today(tenant: Tenant) -> date:
    """Today's calendar date in the tenant's own timezone.

    MUST NOT be derived from UTC: these shops run at UTC+5, where after 19:00
    local the UTC date is still yesterday, so a payment due today would drop out
    of the day's work for its last five hours. Every date-boundary decision in
    the collections queue keys off this.
    """
    return datetime.now(ZoneInfo(_tenant_tz_name(tenant))).date()

def _require_owner_or_manager(membership: TenantUser | None) -> None:
    if membership is None or membership.role not in (TenantRole.owner, TenantRole.manager):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_or_manager_only")

def _require_owner(membership: TenantUser | None) -> None:
    if membership is None or membership.role != TenantRole.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only")

# ── customers ──────────────────────────────────────────────────────────────

# (#810) Hard upper bounds so a single request can't persist absurd values that
# overflow a column (money is Numeric(14,2) ≈ 1e12) or exhaust memory / PDF render.
# The central DataError→422 handler in main.py is the backstop behind these.
_MAX_MONEY = Decimal("999999999999")      # fits Numeric(14,2)

_MAX_UNIT_PRICE = Decimal("999999999.99")

_MAX_QTY = 10_000_000

_MAX_ITEMS = 1000

class CustomerIn(BaseModel):
    full_name:       str = Field(min_length=2, max_length=120)
    phone:           UzPhone = Field(min_length=4, max_length=32)
    secondary_phone: UzPhoneOpt = Field(default=None, max_length=32)
    # Modern split fields — legacy `passport_id` still accepted for back-compat.
    passport_series: str | None = Field(default=None, max_length=8)
    passport_number: str | None = Field(default=None, max_length=16)
    passport_id:     str | None = Field(default=None, max_length=32)
    jshshir:         str | None = Field(default=None, min_length=14, max_length=14, pattern=r"^\d{14}$")
    birthdate:       date | None = None
    address:         str | None = Field(default=None, max_length=500)
    notes:           str | None = Field(default=None, max_length=1000)
    telegram_id:     int | None = None
    employment_place: str | None = Field(default=None, max_length=200)
    monthly_income:   Decimal | None = Field(default=None, ge=0, le=_MAX_MONEY)
    mehnat_daftarchasi_photo_urls: list[str] | None = None

def _derive_passport_id(series: str | None, number: str | None, fallback: str | None) -> str | None:
    if series and number:
        return f"{series.upper().strip()}{number.strip()}"
    return fallback
