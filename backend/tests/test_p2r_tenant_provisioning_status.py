"""Phase 3 remediation pass 2, P2-01 — provisioning must not start a trial.

`create_tenant` hard-coded ``status=TenantStatus.demo``. `demo` is not a label:
it is the trial lifecycle. ``expire_demos`` selects on
``Tenant.status == demo`` and flips those tenants to suspended (read-only) or
blocked once ``trial_ends_at`` passes, and ``plan_limits`` swaps a demo tenant's
caps for the ``demo_plan`` config rather than its plan's. So every shop the
operator provisioned — on a paid plan, with trials deferred by Phase 2 — was
being created inside a lifecycle the MVP does not run.

The correct status is the one `set_plan` already applies when a tenant moves off
trial: a non-trial plan means ``active`` with the trial fields cleared. These
tests exercise the real handler against a real database and assert the persisted
row, not the source.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Tenant, User
from app.models.enums import PlatformRole, TenantStatus


async def _create(session, *, plan_code: str, name: str, slug: str) -> Tenant:
    """Drive the real endpoint handler and return the persisted tenant."""
    from app.api.platform import TenantIn, create_tenant

    admin = User(telegram_id=abs(hash(slug)) % 10_000_000, platform_role=PlatformRole.super_admin,
                 is_active=True)
    session.add(admin)
    await session.flush()

    body = TenantIn(name=name, slug=slug, plan_code=plan_code)
    result = await create_tenant(body=body, user=admin, db=session)

    return (await session.execute(
        select(Tenant).where(Tenant.id == result["id"])
    )).scalar_one()


@pytest.mark.parametrize("plan_code", ["lite", "pro", "custom"])
async def test_paid_plan_provisions_an_active_tenant(migrated_db, plan_code: str) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = await _create(s, plan_code=plan_code, name=f"Shop {plan_code}", slug=f"shop-{plan_code}")

    assert t.status is TenantStatus.active, (
        f"a {plan_code} tenant must not be created inside the demo/trial lifecycle"
    )
    assert t.status is not TenantStatus.demo
    # Nothing may schedule an expiry for a production tenant.
    assert t.trial_ends_at is None
    assert t.suspended_at is None


async def test_the_trial_tier_still_enters_the_demo_lifecycle(migrated_db) -> None:
    """Legacy behaviour is preserved for the one plan that genuinely is a trial."""
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = await _create(s, plan_code="trial", name="Trial Shop", slug="trial-shop")
    assert t.status is TenantStatus.demo


async def test_a_new_tenant_is_not_picked_up_by_the_demo_expiry_job(migrated_db) -> None:
    """The end-to-end consequence, stated as the job itself sees it.

    `expire_demos` selects `status == demo AND trial_ends_at < now`. A tenant
    provisioned on a paid plan must not match that predicate under any clock.
    """
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        await _create(s, plan_code="lite", name="Not A Trial", slug="not-a-trial")

    async with Session() as s:
        candidates = (await s.execute(
            select(Tenant).where(
                Tenant.status == TenantStatus.demo,
                Tenant.trial_ends_at.is_not(None),
            )
        )).scalars().all()
    assert [c.slug for c in candidates] == [], (
        "a paid-plan tenant must never be a candidate for demo expiry"
    )


async def test_legacy_demo_rows_are_untouched(migrated_db) -> None:
    """Provisioning a new tenant must not rewrite existing demo tenants."""
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        legacy = Tenant(slug="legacy-demo", name="Legacy Demo", status=TenantStatus.demo)
        s.add(legacy)
        await s.commit()

    async with Session() as s:
        await _create(s, plan_code="lite", name="Fresh Shop", slug="fresh-shop")

    async with Session() as s:
        still = (await s.execute(
            select(Tenant).where(Tenant.slug == "legacy-demo")
        )).scalar_one()
    assert still.status is TenantStatus.demo, "legacy demo tenants stay readable as-is"
