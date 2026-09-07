"""M-2 — a duplicate value must say which field is duplicated.

THE DEFECT (2026-08-08 QA pass). Every unique-constraint conflict came back as
the generic code, so the client rendered "Bu ma'lumot allaqachon o'zgargan —
sahifani yangilab, qayta ko'ring" ("this data has changed, refresh the page"):

    duplicate SKU            -> 409 {"detail":"conflict"}
    duplicate customer phone -> 409 {"detail":"conflict"}

That is the wrong diagnosis AND useless advice — nothing changed underneath the
user, they typed a value someone else already holds, which they can fix in two
seconds if anyone tells them.

ROOT CAUSE. `_integrity_error_handler` read `exc.orig.constraint_name`, but
under asyncpg `exc.orig` is the DBAPI wrapper, which has no such attribute. The
real error sits one level deeper:

    exc.orig              -> AsyncAdapt_asyncpg_dbapi.IntegrityError   (no name)
    exc.orig.__cause__    -> UniqueViolationError, constraint_name='uq_…'

So `_INTEGRITY_CODES` never matched a single row and the whole feature had been
dead since it was written in #87 — including the frontend copy for it, which is
correct but was unreachable.

WHY THE EXISTING GUARD MISSED IT. `integrityCodes.contract.test.ts` compares the
MAP against the frontend's keys. Both sides were right; only the lookup between
them was broken. These tests provoke a REAL violation through the API and assert
on the response body, which is the only thing that could have caught it.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def ctx():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            t = Tenant(slug="dup-shop", name="Dup Shop")
            s.add(t); await s.flush()
            owner = User(telegram_id=340001)
            s.add(owner); await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
            await s.commit()
            auth = {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}",
                    "X-Tenant-Id": str(t.id)}
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, auth
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def test_a_duplicate_customer_phone_names_the_field(ctx) -> None:
    """The constraint is a functional unique INDEX (r96) — the handler must read
    the name off it just the same."""
    client, auth = ctx
    body = {"full_name": "First Person", "phone": "+998901234567"}
    r = await client.post("/api/tenant/customers", headers=auth, json=body)
    assert r.status_code == 201, r.text[:150]

    r = await client.post("/api/tenant/customers", headers=auth,
                          json={"full_name": "Second Person", "phone": "998901234567"})
    assert r.status_code == 409, r.text[:150]
    assert r.json()["detail"] == "customer_phone_exists", (
        "a duplicate phone still reports the generic 'conflict', so the client "
        "tells the user to refresh the page instead of naming the field"
    )


async def test_a_duplicate_sku_names_the_field(ctx) -> None:
    """A true CONSTRAINT (not an index) — the same lookup must work for both."""
    client, auth = ctx
    body = {"name_uz": "Phone A", "sku": "DUP-SKU-1", "base_price": 100000, "stock": 1}
    r = await client.post("/api/tenant/products", headers=auth, json=body)
    assert r.status_code == 201, r.text[:150]

    r = await client.post("/api/tenant/products", headers=auth,
                          json={**body, "name_uz": "Phone B"})
    assert r.status_code == 409, r.text[:150]
    assert r.json()["detail"] == "product_sku_exists"


async def test_a_duplicate_guarantor_phone_names_the_field(ctx) -> None:
    client, auth = ctx
    body = {"full_name": "Kafil Bir", "phone": "+998907654321"}
    r = await client.post("/api/tenant/guarantors", headers=auth, json=body)
    assert r.status_code == 201, r.text[:150]

    r = await client.post("/api/tenant/guarantors", headers=auth,
                          json={"full_name": "Kafil Ikki", "phone": "907654321"})
    assert r.status_code == 409, r.text[:150]
    assert r.json()["detail"] == "guarantor_phone_exists"


async def test_an_unmapped_constraint_still_degrades_to_a_clean_conflict(ctx) -> None:
    """The fallback must survive: an unknown constraint is still a 409 with a
    generic code, never a 500 and never a mislabelled duplicate."""
    from unittest.mock import Mock

    from fastapi import Request
    from sqlalchemy.exc import IntegrityError

    from app.main import _integrity_error_handler

    cause = Mock(); cause.constraint_name = "uq_something_nobody_mapped"
    orig = Mock(spec=[]); orig.__cause__ = cause
    exc = IntegrityError("stmt", {}, orig)
    req = Mock(spec=Request); req.url = Mock(); req.url.path = "/api/x"

    resp = await _integrity_error_handler(req, exc)
    assert resp.status_code == 409
    assert b"conflict" in resp.body


async def test_the_handler_reads_through_the_asyncpg_wrapper() -> None:
    """Pins the exact indirection that was missing, without needing a database.

    `exc.orig` is the DBAPI wrapper and carries no `constraint_name`; the real
    asyncpg error hangs off `__cause__`. Reading only the first level is what
    silently disabled every mapped code.
    """
    from unittest.mock import Mock

    from fastapi import Request
    from sqlalchemy.exc import IntegrityError

    from app.main import _integrity_error_handler

    cause = Mock(); cause.constraint_name = "uq_customer_tenant_phone"
    orig = Mock(spec=[])          # spec=[] → no attributes, like the real wrapper
    orig.__cause__ = cause
    exc = IntegrityError("stmt", {}, orig)
    req = Mock(spec=Request); req.url = Mock(); req.url.path = "/api/tenant/customers"

    resp = await _integrity_error_handler(req, exc)
    assert b"customer_phone_exists" in resp.body, (
        "the handler is still reading only exc.orig and missing the real error"
    )
