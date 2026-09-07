"""The application, over HTTP, with row-level security actually switched on.

Everything else in the RLS suite proves what the DATABASE does. This proves the
product still works when it does — which is the half that a fail-closed rewrite
gets wrong. A policy that returns nothing is perfectly secure and completely
broken, and the only way to tell those apart is to drive the real endpoints
through the real dependency chain with ``rls_enabled=True``.

So each test here is a pair: the legitimate caller still gets their data, and
the illegitimate one gets nothing.
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests import _dbcreds

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(loop_scope="module")
async def client(rls_world, monkeypatch):
    """An ASGI client whose sessions run under the real authority machinery."""
    import app.db as db
    from app.config import settings
    from app.db import get_db
    from app.main import app

    engine = create_async_engine(_dbcreds.render(rls_world.engine.url), pool_size=2, max_overflow=0)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "rls_enabled", True)

    async def _override():
        async with Session() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override
    db.reset_db_scope()
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.reset_db_scope()
        await engine.dispose()


def _auth(user_id: str) -> dict[str, str]:
    from app.core.security import issue_token

    return {"Authorization": f"Bearer {issue_token(user_id=user_id)}"}


# ── the tenant back office still works, and still only sees itself ───────────

async def test_an_owner_lists_their_own_customers_and_nobody_elses(client, rls_world):
    r = await client.get("/api/tenant/customers", headers=_auth(rls_world.owner_a))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [rls_world.customer_a]

    r = await client.get("/api/tenant/customers", headers=_auth(rls_world.owner_b))
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["items"]] == [rls_world.customer_b]


async def test_a_spoofed_tenant_header_grants_nothing(client, rls_world):
    """``X-Tenant-Id`` selects among the tenants you are a member of. Naming one
    you are not a member of is refused by the dependency, and — the part this
    batch adds — the database would refuse it too, because the header never
    reaches ``app.tenant_id`` unless the membership check passed first."""
    r = await client.get(
        "/api/tenant/customers",
        headers={**_auth(rls_world.owner_a), "X-Tenant-Id": rls_world.tenant_b},
    )
    assert r.status_code in (403, 404), r.text
    assert rls_world.customer_b not in r.text


async def test_a_cross_tenant_id_is_a_uniform_not_found(client, rls_world):
    """Guessing another shop's UUID must be indistinguishable from a bad id."""
    a = await client.get(
        f"/api/tenant/customers/{rls_world.customer_b}",
        headers=_auth(rls_world.owner_a),
    )
    b = await client.get(
        "/api/tenant/customers/00000000-0000-0000-0000-0000000000ff",
        headers=_auth(rls_world.owner_a),
    )
    assert a.status_code == b.status_code == 404
    assert a.json() == b.json()


# ── the customer surface ─────────────────────────────────────────────────────

async def test_a_verified_customer_sees_their_contract(client, rls_world):
    r = await client.get("/api/me/summary", headers=_auth(rls_world.customer_user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["installments_count"] == 1, body

    r = await client.get("/api/me/installments", headers=_auth(rls_world.customer_user))
    assert r.status_code == 200, r.text
    ids = [i["id"] for i in r.json()]
    assert ids == [rls_world.installment_a]


async def test_an_unverified_account_sees_no_contracts(client, rls_world):
    r = await client.get("/api/me/installments", headers=_auth(rls_world.unverified_user))
    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_a_shop_owner_is_not_a_customer_of_their_own_shop(client, rls_world):
    r = await client.get("/api/me/installments", headers=_auth(rls_world.owner_a))
    assert r.status_code == 200, r.text
    assert r.json() == []


# ── authentication still works under a fail-closed users policy ──────────────

async def test_authentication_survives_the_fail_closed_user_policy(client, rls_world):
    """``current_user`` reads the account BEFORE any tenant is resolved. It works
    because the token's subject is pinned into ``app.user_id`` first — without
    that, the fail-closed ``users`` policy would 401 every request in the
    product."""
    r = await client.get("/api/me/identity", headers=_auth(rls_world.customer_user))
    assert r.status_code == 200, r.text

    r = await client.get("/api/me/identity")
    assert r.status_code == 401


# ── platform HQ ──────────────────────────────────────────────────────────────

async def test_a_tenant_owner_cannot_enter_platform_hq(client, rls_world):
    r = await client.get("/api/platform/overview", headers=_auth(rls_world.owner_a))
    assert r.status_code == 403, r.text
    assert rls_world.tenant_b not in r.text


async def test_platform_hq_sees_every_shop(client, rls_world):
    """The elevation is real: with the platform role, HQ spans tenants again."""
    from sqlalchemy import text

    async with rls_world.engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET platform_role = 'super_admin' WHERE id = :u"),
            {"u": rls_world.owner_a},
        )
    try:
        r = await client.get("/api/platform/overview", headers=_auth(rls_world.owner_a))
        assert r.status_code == 200, r.text
        assert r.json()["tenants_total"] == 2, r.text
    finally:
        async with rls_world.engine.begin() as conn:
            await conn.execute(
                text("UPDATE users SET platform_role = NULL WHERE id = :u"),
                {"u": rls_world.owner_a},
            )


# ── capability surfaces ──────────────────────────────────────────────────────

async def test_a_signed_receipt_link_still_serves_its_blob(client, rls_world):
    """The signed URL carries no session, so before Batch 5 it ran unscoped —
    which under the old policies meant every tenant's rows. It now narrows to the
    blob's own shop and still serves the bytes."""
    from app.core.security import sign_receipt

    sig = sign_receipt(rls_world.receipt_a)
    r = await client.get(f"/api/receipts/{rls_world.receipt_a}?s={sig}")
    assert r.status_code == 200, r.text
    assert r.content == b"A"


async def test_a_receipt_link_with_a_bad_signature_is_refused(client, rls_world):
    r = await client.get(f"/api/receipts/{rls_world.receipt_a}?s=deadbeef")
    assert r.status_code == 403


async def test_an_invitation_credential_still_opens_its_own_invitation(
    client, rls_world,
):
    r = await client.get(f"/api/inv/{rls_world.invitation_verifier_a}")
    assert r.status_code == 200, r.text
    assert r.json()["tenant_slug"] == "rls-a"


async def test_an_unknown_invitation_credential_is_not_found(client, rls_world):
    r = await client.get("/api/inv/definitely-not-a-real-credential")
    assert r.status_code == 404


# ── the public/reference surfaces a tenant depends on ────────────────────────

async def test_the_shared_catalogue_is_still_readable_by_a_tenant(client, rls_world):
    r = await client.get("/api/catalog/categories", headers=_auth(rls_world.owner_a))
    assert r.status_code == 200, r.text


async def test_the_plan_catalogue_is_still_readable(client, rls_world):
    r = await client.get("/api/plans/public", headers=_auth(rls_world.owner_a))
    assert r.status_code == 200, r.text


# ── the database is the boundary, not the dependency ─────────────────────────

async def test_a_route_that_forgets_resolve_tenant_returns_no_rows(client, rls_world):
    """The brief's requirement, proved over HTTP rather than by inspection.

    ``tests/test_rls_route_contract.py`` catches a missing ``resolve_tenant``
    statically, and that test is useful — but it is a lint, not a boundary. This
    registers a real route that really forgets, calls it with a real owner's
    token, and requires the DATABASE to return nothing.

    Before Batch 5 this same route would have returned every tenant's customers,
    because an unscoped transaction ran as the schema owner.
    """
    from fastapi import APIRouter, Depends
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.security import current_user
    from app.db import get_db
    from app.main import app

    rogue = APIRouter()

    @rogue.get("/api/tenant/rls-forgot-the-dependency")
    async def rls_forgot_the_dependency(
        _user=Depends(current_user), db: AsyncSession = Depends(get_db),
    ) -> dict:
        rows = (await db.execute(_text("SELECT id FROM customers"))).scalars().all()
        return {"ids": [str(r) for r in rows]}

    app.include_router(rogue)
    try:
        r = await client.get(
            "/api/tenant/rls-forgot-the-dependency",
            headers=_auth(rls_world.owner_a),
        )
        assert r.status_code == 200, r.text
        assert r.json()["ids"] == [], (
            "a route with no tenant authorization read customer rows — the "
            "database is supposed to be the boundary, not the dependency"
        )
    finally:
        app.router.routes = [
            route for route in app.router.routes
            if getattr(route, "name", None) != "rls_forgot_the_dependency"
        ]


async def test_the_same_route_with_a_tenant_scope_does_return_rows(client, rls_world):
    """The counterpart, so the test above cannot pass because the route is broken.

    Identical handler, one dependency added. If this returned nothing too, the
    assertion above would be measuring a typo rather than a policy.
    """
    from fastapi import APIRouter, Depends
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.tenant import resolve_tenant
    from app.db import get_db
    from app.main import app

    scoped = APIRouter()

    @scoped.get("/api/tenant/rls-remembered-the-dependency")
    async def rls_remembered_the_dependency(
        _ctx=Depends(resolve_tenant), db: AsyncSession = Depends(get_db),
    ) -> dict:
        rows = (await db.execute(_text("SELECT id FROM customers"))).scalars().all()
        return {"ids": [str(r) for r in rows]}

    app.include_router(scoped)
    try:
        r = await client.get(
            "/api/tenant/rls-remembered-the-dependency",
            headers=_auth(rls_world.owner_a),
        )
        assert r.status_code == 200, r.text
        assert r.json()["ids"] == [rls_world.customer_a]
    finally:
        app.router.routes = [
            route for route in app.router.routes
            if getattr(route, "name", None) != "rls_remembered_the_dependency"
        ]


# ── invitations cannot be redeemed across the boundary ───────────────────────

async def test_an_invitation_credential_reaches_only_its_own_shop(client, rls_world):
    """Holding shop A's credential grants shop A, and nothing of shop B.

    The credential is a bearer capability presented by somebody who is not yet a
    member of anything, so it is the one place a request legitimately gains a
    tenant scope without a membership check. It must therefore grant exactly one
    shop — the invitation's own.
    """
    from sqlalchemy import text as _text

    r = await client.get(f"/api/inv/{rls_world.invitation_verifier_a}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_slug"] == "rls-a"
    assert "rls-b" not in r.text
    assert rls_world.tenant_b not in r.text

    # And at the database level: unlocking A's credential pins A's scope only,
    # so B's invitation stays invisible for the rest of that request.
    from app.core import invite_codes
    from app.db import reset_db_scope

    reset_db_scope()
    async with rls_world.engine.connect() as conn:
        await conn.rollback()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db as db
    from app.config import settings

    engine = create_async_engine(_dbcreds.render(rls_world.engine.url))
    Session = async_sessionmaker(engine, expire_on_commit=False)
    was = settings.rls_enabled
    settings.rls_enabled = True
    try:
        async with Session() as session:
            tenant = await invite_codes.unlock(
                session, rls_world.invitation_verifier_a,
            )
            assert tenant == rls_world.tenant_a
            visible = (await session.execute(
                _text("SELECT id FROM invitations"))).scalars().all()
            assert [str(i) for i in visible] == [rls_world.invitation_a], (
                "an invitation credential exposed another shop's invitation"
            )
    finally:
        settings.rls_enabled = was
        db.reset_db_scope()
        await engine.dispose()


async def test_an_unknown_credential_unlocks_nothing_at_all(client, rls_world):
    """A wrong credential is indistinguishable from one that never existed, and
    it grants no scope — so a guesser gains neither an oracle nor a foothold."""
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db as db
    from app.config import settings
    from app.core import invite_codes

    engine = create_async_engine(_dbcreds.render(rls_world.engine.url))
    Session = async_sessionmaker(engine, expire_on_commit=False)
    was = settings.rls_enabled
    settings.rls_enabled = True
    try:
        async with Session() as session:
            assert await invite_codes.unlock(session, "not-a-real-credential") is None
            visible = (await session.execute(
                _text("SELECT id FROM invitations"))).scalars().all()
            assert visible == []
            customers = (await session.execute(
                _text("SELECT count(*) FROM customers"))).scalar_one()
            assert customers == 0
    finally:
        settings.rls_enabled = was
        db.reset_db_scope()
        await engine.dispose()
