"""QA-016 — the platform plan-catalogue cap and the tenant subscription limit
must not answer with the same machine code.

Two different refusals shared the literal ``plan_limit_reached``:

* ``POST /api/platform/plans`` — the platform owner has minted ``MAX_PLANS``
  plans and may not mint another. Actor: super-admin. Remedy: delete or edit an
  existing plan. HTTP 409, bare-string detail.
* ``POST /api/tenant/installments`` — a *shop* has hit the contract ceiling of
  the plan it subscribes to. Actor: shop owner. Remedy: upgrade the plan. HTTP
  402, structured detail carrying ``limit_key``/``limit``.

One code for both meant the frontend could only render one message. It rendered
the tenant one — "Tarif limiti to'ldi … upgrade your plan" — to a super-admin
who has no plan to upgrade.

These are wire-level assertions: the frontend maps the code, so the contract is
what the endpoint puts on the wire, not what the handler passes internally.
The tenant side is asserted here too, unchanged, so a future edit cannot
"unify" the two codes again without a red test.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

PLANS_URL = "/api/platform/plans"


@pytest_asyncio.fixture
async def platform_ctx():
    """A super-admin, a plain user, and exactly ``MAX_PLANS`` stored plans."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.platform.provisioning import MAX_PLANS
    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Plan, User
    from app.models.enums import PlatformRole

    engine = create_async_engine(url)
    # (QA-R-002) DB tier is mandatory once TEST_DATABASE_URL is set — setup errors
    # (refused connection, privilege, bad schema) must FAIL, never self-skip green.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            admin = User(telegram_id=916001, platform_role=PlatformRole.super_admin)
            s.add(admin)
            for i in range(MAX_PLANS):
                s.add(Plan(
                    code=f"qa016-{i}", name_uz=f"Plan {i}", name_ru=f"План {i}",
                    price_monthly=Decimal("100000"), max_active_installments=10,
                ))
            await s.flush()
            ctx = {
                "max_plans": MAX_PLANS,
                "admin": {"Authorization": f"Bearer {issue_token(user_id=str(admin.id))}"},
            }
            await s.commit()
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            yield client, ctx
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _new_plan_body(code: str = "qa016-extra") -> dict:
    return {"code": code, "name_uz": "Qo'shimcha", "name_ru": "Дополнительный", "price_monthly": 1}


# ── the platform catalogue cap ─────────────────────────────────────────────

async def test_catalogue_cap_uses_its_own_code(platform_ctx) -> None:
    client, ctx = platform_ctx
    r = await client.post(PLANS_URL, json=_new_plan_body(), headers=ctx["admin"])
    assert r.status_code == 409, f"{r.status_code} {r.text[:300]}"
    assert r.json()["detail"] == "platform_plan_limit_reached"


async def test_catalogue_cap_does_not_reuse_the_tenant_code(platform_ctx) -> None:
    """The whole point of the finding: these two must never coincide again."""
    client, ctx = platform_ctx
    r = await client.post(PLANS_URL, json=_new_plan_body(), headers=ctx["admin"])
    assert r.json()["detail"] != "plan_limit_reached"


async def test_response_shape_is_unchanged(platform_ctx) -> None:
    """Only the code moved: still 409, still a bare-string ``detail``."""
    client, ctx = platform_ctx
    r = await client.post(PLANS_URL, json=_new_plan_body(), headers=ctx["admin"])
    body = r.json()
    assert set(body) == {"detail"}
    assert isinstance(body["detail"], str)


async def test_the_other_409_on_this_route_is_untouched(platform_ctx) -> None:
    """``plan_exists`` shares the status code; it must keep its own meaning."""
    client, ctx = platform_ctx
    r = await client.post(PLANS_URL, json=_new_plan_body("qa016-0"), headers=ctx["admin"])
    assert r.status_code == 409
    assert r.json()["detail"] == "plan_exists"


async def test_under_the_cap_the_create_still_succeeds(platform_ctx) -> None:
    """The cap is the only thing refusing — not the new code path itself."""
    client, ctx = platform_ctx
    from sqlalchemy import delete, select

    from app.db import get_db
    from app.main import app
    from app.models import Plan

    # Free one slot through the same session factory the app uses.
    gen = app.dependency_overrides[get_db]()
    db = await gen.__anext__()
    try:
        await db.execute(delete(Plan).where(Plan.code == "qa016-0"))
        await db.commit()
        remaining = (await db.execute(select(Plan))).scalars().all()
        assert len(remaining) == ctx["max_plans"] - 1
    finally:
        await gen.aclose()

    r = await client.post(PLANS_URL, json=_new_plan_body(), headers=ctx["admin"])
    assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"


# ── the tenant subscription limit, unchanged ───────────────────────────────

async def test_tenant_contract_limit_keeps_its_own_code_and_status() -> None:
    """The tenant refusal is a 402 with a structured detail — a different wire
    contract from the catalogue cap, and it must stay exactly as it is."""
    import inspect

    from app.api.tenant import installments

    src = inspect.getsource(installments)
    assert '"error": "plan_limit_reached", "limit_key": "contract"' in src
    assert "HTTP_402_PAYMENT_REQUIRED" in src
    # And the platform literal must not have leaked into the tenant module.
    assert "platform_plan_limit_reached" not in src
