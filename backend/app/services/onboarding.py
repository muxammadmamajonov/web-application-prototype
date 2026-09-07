"""Tenant onboarding — turns a redeemed demo intent into a real Tenant."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plan, Tenant, TenantSettings, TenantSubscription, TenantUser, User
from app.models.enums import (
    SubscriptionStatus,
    TenantRole,
    TenantStatus,
)


def _slugify(value: str, *, fallback: str = "biznes") -> str:
    s = value.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or fallback


async def _unique_slug(db: AsyncSession, base: str) -> str:
    candidate = base
    tries = 0
    while True:
        exists = (await db.execute(select(Tenant.id).where(Tenant.slug == candidate))).first()
        if not exists:
            return candidate
        tries += 1
        candidate = f"{base}-{tries}"


async def provision_tenant_from_intent(
    db: AsyncSession, *, user: User, intent: dict[str, Any], trial_days: int | None = None,
) -> Tenant:
    """Create Tenant + owner membership + trial subscription for the given user.

    ``trial_days`` defaults to the platform's ``demo_plan`` config when not given.
    """
    # (#209) A platform operator (super_admin/support) must not self-provision a
    # customer tenant — that mixes the operator identity with a shop-owner one.
    if user.platform_role is not None:
        raise ValueError("platform_admin_cannot_provision_tenant")
    if trial_days is None:
        from app.services.demo_plan import get_config
        trial_days = int((await get_config(db)).get("trial_days") or 14)
    # Free-form plan code (R36). The literal that used to stand here was "lite",
    # a code the catalogue dropped in the same round that made codes owner-chosen
    # — so every shop provisioned through this path was stamped with a plan no
    # `plans` row matched, and the subscription insert below silently found
    # nothing to insert. Resolution is delegated so the platform's configured
    # "Standart tarif" is what actually applies, and the last resort is the
    # catalogue's own first active plan rather than another literal that can rot.
    from app.services.platform_defaults import resolve_plan_code

    requested = intent.get("plan_code")
    plan_code = await resolve_plan_code(
        db, requested if isinstance(requested, str) else None
    )
    if plan_code is None:
        # No plan named, and no catalogue to choose from. Self-serve signup onto
        # a plan that does not exist is what this whole change removes.
        raise ValueError("no_plan_catalogue")

    base_slug = _slugify(intent.get("business_name", ""))
    slug = await _unique_slug(db, base_slug)

    now = datetime.now(UTC)
    tenant = Tenant(
        slug=slug,
        name=intent.get("business_name") or "Demo biznes",
        status=TenantStatus.demo,
        plan_code=plan_code,
        contact_phone=intent.get("phone") or intent.get("contact_phone"),
        locale=intent.get("locale", "uz"),
        business_type=intent.get("business_type"),
        business_type_other=intent.get("business_type_other"),
        how_found=intent.get("how_found"),
        how_found_other=intent.get("how_found_other"),
        trial_ends_at=now + timedelta(days=trial_days),
    )
    db.add(tenant)
    await db.flush()  # need tenant.id

    db.add(TenantUser(
        tenant_id=tenant.id,
        user_id=user.id,
        role=TenantRole.owner,
        is_active=True,
    ))
    # (#14) Seed the Business Profile name from the business we just created,
    # so the owner's first visit to Settings shows their shop's name rather than
    # an empty field they have to retype exactly as the admin already did.
    db.add(TenantSettings(tenant_id=tenant.id, business_name=tenant.name))

    # (#168) Record the owner's terms acceptance at provision (clickwrap — signing up
    # is what provisions the tenant). Lawful basis = contract (necessary to provide
    # the service). The binding text + basis are in docs/CONSENT.md [LEGAL_REVIEW].
    from app.services.consent import record_consent
    await record_consent(
        db, subject_kind="user", subject_id=user.id, kind="terms",
        lawful_basis="contract", tenant_id=tenant.id, source="signup",
    )

    # `plan_code` was resolved against the catalogue above, so this lookup finds a
    # row unless the plan was deleted in between. The old `"lite"` fallback here
    # could not help — it named the very code that had stopped existing.
    plan = (await db.execute(select(Plan).where(Plan.code == plan_code))).scalar_one_or_none()
    if plan is not None:
        db.add(TenantSubscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status=SubscriptionStatus.trial,
            starts_at=now,
            ends_at=now + timedelta(days=trial_days),
            amount=plan.price_monthly,
            notes=f"Demo trial · {trial_days} days",
        ))

    await db.commit()
    await db.refresh(tenant)
    return tenant
