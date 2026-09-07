"""Idempotent tenant bootstrap — converge a tenant to a known-good baseline
without disturbing anything an owner has already customised (MUDD-137).

Usage:
    uv run python -m scripts.bootstrap_tenant --config <path/to/tenant.json>
    uv run python -m scripts.bootstrap_tenant --config <path/to/tenant.json> --dry-run

Config file (JSON). Keep it OUTSIDE this repo (or in a git-ignored path) — it
names a real slug/phone/business type and must never be committed:

    {
      "slug": "demo-shop-a",
      "name": "Demo Shop A",
      "plan_code": "lite",              // optional — platform default if omitted
      "locale": "uz",                    // "uz" | "ru", only used on first create
      "contact_phone": "+998901234567",  // optional
      "billing_email": null,             // optional
      "business_type": "phone",          // optional
      "mint_owner_invite": true          // mint a claim link IF the tenant has
                                          // no member yet; false to skip
    }

Idempotency contract — re-running this against a tenant with live data is safe:

  * Upsert-by-slug. A slug that already exists is never re-created or
    suffix-duplicated — this script targets exactly one row for the life of a
    tenant (unlike the platform HQ "create shop" form, which always mints a
    fresh slug).
  * On an EXISTING tenant, only NULL/blank fields are filled in
    (contact_phone, billing_email, business_type, business_type_other).
    name, locale, plan_code and status are NEVER overwritten on an existing
    row — those are business config an owner may already have changed (Stage 2
    territory, MUDD-125), and clobbering them on every re-run is exactly the
    failure mode this script exists to avoid.
  * Ensures exactly one `tenant_settings` row exists (created with column
    defaults if missing). An existing row is never touched.
  * Mints an owner invite only when the tenant has zero memberships — an
    already-claimed shop is never re-invited, so re-running never orphans an
    owner's session or issues a second, competing claim link.
  * Never reads or writes installments, payments, contracts, or any other
    money-affecting row. Balance/subscription/plan changes stay out of scope —
    use the platform HQ dashboard for those; it already audits them.
  * A fresh tenant is recorded with the same `tenant.create` audit action the
    HQ dashboard uses (see app/services/audit.py), so a bootstrap-provisioned
    shop leaves an identical trail to a manually-provisioned one.
  * --dry-run prints the exact plan (create | field-fill | invite | noop) and
    performs zero writes and zero commits.

Proving idempotency: run once, run again with the SAME config, and diff the
printed plan — the second run's plan must contain only "already exists" /
"untouched" / "SKIP" lines, never a CREATE or SET. This MUST be run through the
restricted `muddato_api` login with RLS enabled, the same posture production
uses — running it as the schema owner (or with RLS off) proves nothing about
whether the script can write in production, only that its Python logic is
correct. See docs/RUNBOOK.md's "Tenant bootstrap" section for the exact
commands used to verify this against that credential.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import DbAuthority, authority_session, engine
from app.models import Invitation, Tenant, TenantSettings, TenantUser, User
from app.models.enums import PlatformRole, TenantStatus

# Fields that may be *filled in* on an existing tenant when currently blank.
# Anything not in this list (name, locale, plan_code, status, ...) is either
# create-only or entirely out of this script's scope — see the module docstring.
FILLABLE_FIELDS: tuple[str, ...] = (
    "contact_phone", "billing_email", "business_type", "business_type_other",
)


async def _resolve_actor(db: Any) -> User | None:
    """The platform admin to attribute a fresh tenant's audit row to.

    Best-effort: an operator running this from a shell has no logged-in user,
    so we attribute to whichever super_admin exists (seed_platform_admin.py
    creates exactly one for a fresh deploy). None is a valid, nullable value
    if there isn't one yet — the row is still written, just unattributed.
    """
    return (await db.execute(
        select(User).where(User.platform_role == PlatformRole.super_admin).limit(1)
    )).scalars().first()


def _print_plan(slug: str, plan: list[str]) -> None:
    print(f"[bootstrap] tenant={slug}")
    for line in plan:
        print(f"  {line}")


async def bootstrap(config: dict[str, Any], *, dry_run: bool) -> int:
    slug = config["slug"]
    plan: list[str] = []

    async with authority_session(DbAuthority.platform) as db:
        tenant = (await db.execute(
            select(Tenant).where(Tenant.slug == slug)
        )).scalar_one_or_none()

        if tenant is None:
            from app.services.platform_defaults import resolve_plan_code

            plan_code = await resolve_plan_code(db, (config.get("plan_code") or "").strip() or None)
            if plan_code is None:
                print(
                    "[bootstrap] ABORT: no plan catalogue — run seed_plans.py first",
                    file=sys.stderr,
                )
                return 1
            plan.append(f"CREATE tenant slug={slug!r} name={config['name']!r} plan_code={plan_code!r}")
            if dry_run:
                _print_plan(slug, plan)
                return 0

            tenant = Tenant(
                slug=slug,
                name=config["name"],
                plan_code=plan_code,
                status=TenantStatus.demo if plan_code == "trial" else TenantStatus.active,
                locale=config.get("locale", "uz"),
                contact_phone=config.get("contact_phone"),
                billing_email=config.get("billing_email"),
                business_type=config.get("business_type"),
                business_type_other=config.get("business_type_other"),
            )
            db.add(tenant)
            await db.flush()

            actor = await _resolve_actor(db)
            from app.services import audit
            await audit.record(
                db, action="tenant.create", tenant_id=tenant.id,
                actor_user_id=actor.id if actor else None,
                entity_type="tenant", entity_id=str(tenant.id),
                after={"slug": tenant.slug, "plan_code": tenant.plan_code, "source": "bootstrap_tenant"},
            )
        else:
            plan.append(f"tenant slug={slug!r} already exists (id={tenant.id}) — filling blanks only")
            for field in FILLABLE_FIELDS:
                incoming = config.get(field)
                current = getattr(tenant, field)
                if incoming and not current:
                    plan.append(f"  SET {field} = {incoming!r} (was NULL)")
                    if not dry_run:
                        setattr(tenant, field, incoming)
                elif incoming and current and incoming != current:
                    plan.append(f"  SKIP {field}: owner already set {current!r} — never overwritten")

        # tenant_settings: created once with defaults, never edited after —
        # that row is Stage 2's territory (currency, calc formula, payments...).
        has_settings = (await db.execute(
            select(TenantSettings.id).where(TenantSettings.tenant_id == tenant.id)
        )).scalar_one_or_none()
        if has_settings is None:
            plan.append("CREATE tenant_settings row (column defaults)")
            if not dry_run:
                db.add(TenantSettings(tenant_id=tenant.id))
        else:
            plan.append("tenant_settings row already exists — untouched")

        # Owner invite: only if nobody has claimed this shop yet.
        if config.get("mint_owner_invite", True):
            has_member = (await db.execute(
                select(TenantUser.id).where(TenantUser.tenant_id == tenant.id).limit(1)
            )).scalar_one_or_none()
            if has_member is not None:
                plan.append("owner invite SKIPPED — tenant already has a member")
            else:
                # A still-valid, unclaimed invite must NOT be re-minted: minting
                # invalidates the previous one (see _mint_owner_invite), and the
                # raw token is unrecoverable once issued (only its HMAC verifier
                # is stored) — re-running this script between deploys must never
                # silently kill a link an owner hasn't clicked yet.
                pending = (await db.execute(
                    select(Invitation.id).where(
                        Invitation.tenant_id == tenant.id,
                        Invitation.kind == "staff",
                        Invitation.role == "owner",
                        Invitation.used_at.is_(None),
                        Invitation.expires_at > datetime.now(UTC),
                    ).limit(1)
                )).scalar_one_or_none()
                if pending is not None:
                    plan.append(
                        "owner invite SKIPPED — a live, unclaimed invite already "
                        "exists (re-minting would invalidate it before anyone can use it)"
                    )
                else:
                    plan.append("MINT owner invite (tenant has zero members and no live invite)")
                    if not dry_run:
                        actor = await _resolve_actor(db)
                        from app.api.platform.tenants import _mint_owner_invite
                        share_url = await _mint_owner_invite(
                            db, tenant_id=tenant.id,
                            actor_user_id=actor.id if actor else None,
                        )
                        plan.append(f"  share_url = {share_url}")

        _print_plan(slug, plan)

        if dry_run:
            await db.rollback()
            return 0
        await db.commit()

    return 0


async def _run(config: dict[str, Any], *, dry_run: bool) -> int:
    try:
        return await bootstrap(config, dry_run=dry_run)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotently converge a tenant to a known-good baseline.",
    )
    parser.add_argument("--config", required=True, type=Path, help="path to the tenant JSON config")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = parser.parse_args()

    try:
        config = json.loads(args.config.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[bootstrap] cannot read config {args.config}: {exc}", file=sys.stderr)
        sys.exit(2)

    if not config.get("slug") or not config.get("name"):
        print("[bootstrap] config must include at least 'slug' and 'name'", file=sys.stderr)
        sys.exit(2)

    sys.exit(asyncio.run(_run(config, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
