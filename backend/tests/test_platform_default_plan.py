"""The platform's "Standart tarif" must be stored, read back, and APPLIED.

THREE FAILURES, ONE SETTING (production, 2026-08-11).

1. The admin form wrote the whole group to the settings key ``defaults`` and read
   four separate keys (``default_plan`` …). The write landed in a row nothing
   read, so the Select came back empty after a reload. Pinned on the frontend
   (``defaultPlanPersistence.test.tsx``); pinned here from the reader's side, so
   the backend and the form agree about where the value lives.

2. Nothing consumed the setting. Both provisioning paths hardcoded the plan code
   ``"lite"``, which the catalogue dropped in R36 when codes became owner-chosen
   slugs. Every shop provisioned since carried a code no ``plans`` row matches —
   and, because the subscription insert looks the code up, got NO subscription
   row at all: no plan, no limits, no price.

3. Nothing refused a dangling value on the way in, which is why (2) survived: the
   stored default stayed ``lite`` indefinitely with no complaint from any layer.

The assertions therefore run the whole way through — configure, provision, read
the tenant back — rather than checking that a function returns a string.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import Plan, PlatformSetting, Tenant, TenantSubscription, User

pytestmark = pytest.mark.asyncio


def _plan(code: str, *, order: int, active: bool = True) -> Plan:
    return Plan(
        code=code, name_uz=code.title(), name_ru=code.title(),
        price_monthly=Decimal("100000"), sort_order=order, is_active=active,
    )


@pytest_asyncio.fixture
async def session():
    """A plain session on the model-only schema — no HTTP, no RLS."""
    import os

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as s:
            yield s
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# ── the reader ───────────────────────────────────────────────────────────────

async def test_reads_the_group_row_the_form_actually_writes(session) -> None:
    from app.services.platform_defaults import read_defaults

    session.add(PlatformSetting(key="defaults", value={"default_plan": "business", "trial_days": 21}))
    await session.commit()

    got = await read_defaults(session)
    assert got["default_plan"] == "business"
    assert got["trial_days"] == 21


async def test_reads_the_legacy_flat_rows_and_unwraps_scalars(session) -> None:
    """An install configured before the group row keeps its configuration.

    `PUT /platform/settings/{key}` wraps a non-dict value as ``{"v": …}``, so a
    flat row holds ``{"v": "starter"}`` and not ``"starter"``. A reader that
    forgot the wrapper would report the dict as the plan code.
    """
    from app.services.platform_defaults import read_defaults

    session.add(PlatformSetting(key="default_plan", value={"v": "starter"}))
    await session.commit()

    assert (await read_defaults(session))["default_plan"] == "starter"


async def test_group_row_wins_over_a_stale_flat_row(session) -> None:
    from app.services.platform_defaults import read_defaults

    session.add(PlatformSetting(key="default_plan", value={"v": "starter"}))
    session.add(PlatformSetting(key="defaults", value={"default_plan": "pro"}))
    await session.commit()

    assert (await read_defaults(session))["default_plan"] == "pro"


# ── resolution ───────────────────────────────────────────────────────────────

async def test_resolves_to_the_configured_default(session) -> None:
    from app.services.platform_defaults import resolve_plan_code

    session.add_all([_plan("starter", order=1), _plan("business", order=2)])
    session.add(PlatformSetting(key="defaults", value={"default_plan": "business"}))
    await session.commit()

    assert await resolve_plan_code(session) == "business"


async def test_a_dangling_configured_default_falls_back_to_the_catalogue(session) -> None:
    """`lite` is the real stored value this fix found in production.

    The fallback must be the catalogue's own first ACTIVE plan. Falling back to a
    literal is what produced the bug in the first place.
    """
    from app.services.platform_defaults import resolve_plan_code

    session.add_all([_plan("starter", order=1), _plan("business", order=2)])
    session.add(PlatformSetting(key="defaults", value={"default_plan": "lite"}))
    await session.commit()

    assert await resolve_plan_code(session) == "starter"


async def test_an_inactive_plan_is_never_the_silent_default(session) -> None:
    from app.services.platform_defaults import resolve_plan_code

    session.add_all([_plan("retired", order=0, active=False), _plan("starter", order=1)])
    session.add(PlatformSetting(key="defaults", value={"default_plan": "retired"}))
    await session.commit()

    assert await resolve_plan_code(session) == "starter"


async def test_an_explicit_request_still_wins(session) -> None:
    from app.services.platform_defaults import resolve_plan_code

    session.add_all([_plan("starter", order=1), _plan("pro", order=3)])
    session.add(PlatformSetting(key="defaults", value={"default_plan": "starter"}))
    await session.commit()

    assert await resolve_plan_code(session, "pro") == "pro"


# ── the thing that was actually broken ───────────────────────────────────────

async def test_provisioning_uses_the_configured_default_and_creates_a_subscription(session) -> None:
    """The whole point: configuring a tariff must reach a provisioned shop.

    The subscription assertion is the one that would have caught this in
    production. `plan_code` was set to a dead literal, the plan lookup found
    nothing, and the insert was skipped — so shops existed with no subscription
    row and nobody noticed, because nothing asserted that one appears.
    """
    from app.services.onboarding import provision_tenant_from_intent

    session.add_all([_plan("starter", order=1), _plan("business", order=2)])
    session.add(PlatformSetting(key="defaults", value={"default_plan": "business"}))
    user = User(telegram_id=987001)
    session.add(user)
    await session.commit()

    tenant = await provision_tenant_from_intent(
        session, user=user, intent={"business_name": "Anvar Shop", "locale": "uz"},
    )

    assert tenant.plan_code == "business", "the configured Standart tarif was not applied"
    sub = (await session.execute(
        select(TenantSubscription).where(TenantSubscription.tenant_id == tenant.id)
    )).scalars().first()
    assert sub is not None, "a provisioned shop with no subscription row"
    plan = (await session.execute(select(Plan).where(Plan.id == sub.plan_id))).scalar_one()
    assert plan.code == "business"


async def test_put_settings_refuses_a_default_plan_the_catalogue_lacks(session) -> None:
    """The write-side guard, so a dangling default cannot be stored again.

    Driven through the real endpoint rather than the validator, because the
    dangling value reached the column through `PUT /platform/settings/{key}` and
    the point is that THAT path now refuses it.
    """
    import httpx
    from httpx import ASGITransport

    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models.enums import PlatformRole

    session.add(_plan("starter", order=1))
    admin = User(telegram_id=987003, platform_role=PlatformRole.super_admin)
    session.add(admin)
    await session.commit()

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    try:
        auth = {"Authorization": f"Bearer {issue_token(user_id=str(admin.id))}"}
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            bad = await client.put(
                "/api/platform/settings/defaults",
                json={"value": {"default_plan": "lite"}}, headers=auth,
            )
            assert bad.status_code == 400
            assert bad.json()["detail"]["error"] == "default_plan_unknown"

            good = await client.put(
                "/api/platform/settings/defaults",
                json={"value": {"default_plan": "starter", "trial_days": 7}}, headers=auth,
            )
            assert good.status_code == 200

            # Clearing the field stays legal — "no platform default" is a real
            # configuration, not a validation failure.
            cleared = await client.put(
                "/api/platform/settings/defaults",
                json={"value": {"default_plan": ""}}, headers=auth,
            )
            assert cleared.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)

    row = (await session.execute(
        select(PlatformSetting).where(PlatformSetting.key == "defaults")
    )).scalars().one()
    assert row.value.get("default_plan") != "lite"


async def test_provisioning_never_stamps_a_code_the_catalogue_lacks(session) -> None:
    """Regression on the exact production symptom: `plan_code='lite'`."""
    from app.services.onboarding import provision_tenant_from_intent

    session.add_all([_plan("starter", order=1)])
    user = User(telegram_id=987002)
    session.add(user)
    await session.commit()

    tenant = await provision_tenant_from_intent(
        session, user=user, intent={"business_name": "No Config Shop"},
    )

    codes = {c for (c,) in (await session.execute(select(Plan.code))).all()}
    assert tenant.plan_code in codes
    assert tenant.plan_code != "lite"

    stored = (await session.execute(
        select(Tenant).where(Tenant.id == tenant.id)
    )).scalar_one()
    assert stored.plan_code in codes
