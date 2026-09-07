"""Transaction-context safety for the database authority (Batch 5, Part 5).

The policies are only as good as the scope they read. These tests drive the REAL
``app.db`` machinery with ``rls_enabled`` on and a real pool, and ask the
questions that a policy matrix cannot: does the scope survive a mid-request
commit, does it disappear at the end, can a pooled connection hand tenant A's
scope to tenant B, and can a background job leave its authority behind.

Everything here is about the *lifecycle*. What the scope then permits is
``test_rls_matrix.py``.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests import _dbcreds

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(loop_scope="module")
async def runtime(rls_world, monkeypatch):
    """``app.db`` wired to the test database with RLS actually switched on.

    A small pool on purpose: ``pool_size=1`` guarantees that the "does a pooled
    connection leak the previous tenant" test really does get the same physical
    connection back, rather than passing because it happened to get a fresh one.
    """
    import app.db as db
    from app.config import settings

    engine = create_async_engine(_dbcreds.render(rls_world.engine.url), pool_size=1, max_overflow=0)
    monkeypatch.setattr(settings, "rls_enabled", True)
    monkeypatch.setattr(db, "SessionLocal", async_sessionmaker(
        engine, expire_on_commit=False,
    ))
    db.reset_db_scope()
    try:
        yield db
    finally:
        db.reset_db_scope()
        await engine.dispose()


async def _guc(session) -> tuple[str, str]:
    row = (await session.execute(text(
        "SELECT current_user, coalesce(current_setting('app.tenant_id', true), '')"
    ))).one()
    return row[0], row[1]


# ── the default is the denying one ───────────────────────────────────────────

async def test_a_session_that_asks_for_nothing_gets_the_restricted_role(runtime, rls_world):
    """This is the single most important assertion in the batch.

    Before it, a session with no scope ran as the *connection* role — the schema
    owner — and saw everything. Now it runs as ``muddato_app`` with a blank
    scope, and sees nothing.
    """
    async with runtime.SessionLocal() as s:
        role, tenant = await _guc(s)
        assert role == "muddato_app"
        assert tenant == ""
        seen = (await s.execute(text("SELECT count(*) FROM customers"))).scalar_one()
        assert seen == 0


async def test_scope_applies_immediately_to_the_open_transaction(runtime, rls_world):
    async with runtime.SessionLocal() as s:
        await runtime.set_tenant_scope(s, rls_world.tenant_a)
        role, tenant = await _guc(s)
        assert role == "muddato_app"
        assert tenant == rls_world.tenant_a
        assert (await s.execute(
            text("SELECT count(*) FROM customers"))).scalar_one() == 1


# ── the scope survives a commit, and does not survive the session ────────────

async def test_a_mid_request_commit_does_not_drop_the_scope(runtime, rls_world):
    """A fresh transaction has no SET LOCAL state; ``after_begin`` re-pins it.

    Without this, the second half of any handler that commits mid-way would run
    as the connection role and bypass row security entirely.
    """
    async with runtime.SessionLocal() as s:
        await runtime.set_tenant_scope(s, rls_world.tenant_a)
        await s.commit()
        role, tenant = await _guc(s)
        assert (role, tenant) == ("muddato_app", rls_world.tenant_a)
        assert (await s.execute(
            text("SELECT count(*) FROM customers"))).scalar_one() == 1


async def test_a_rollback_does_not_drop_the_scope_either(runtime, rls_world):
    async with runtime.SessionLocal() as s:
        await runtime.set_tenant_scope(s, rls_world.tenant_a)
        await s.rollback()
        role, tenant = await _guc(s)
        assert (role, tenant) == ("muddato_app", rls_world.tenant_a)


async def test_an_exception_leaves_no_scope_behind_on_the_connection(runtime, rls_world):
    async with runtime.SessionLocal() as s:
        await runtime.set_tenant_scope(s, rls_world.tenant_a)
        from sqlalchemy.exc import DBAPIError

        with pytest.raises(DBAPIError):
            await s.execute(text("SELECT 1/0"))
        await s.rollback()
    runtime.reset_db_scope()
    async with runtime.SessionLocal() as s:
        role, tenant = await _guc(s)
        assert (role, tenant) == ("muddato_app", "")


# ── the pool cannot carry a scope between requests ───────────────────────────

async def test_a_pooled_connection_does_not_hand_tenant_a_to_tenant_b(runtime, rls_world):
    """The same physical connection, twice, with different tenants.

    ``SET LOCAL ROLE`` and ``set_config(..., is_local=true)`` are both undone at
    the end of the transaction, and every GUC is rewritten at the start of the
    next one — including to blank — so neither the role nor the tenant can ride
    a checked-in connection into the next request.
    """
    async with runtime.SessionLocal() as s:
        await runtime.set_tenant_scope(s, rls_world.tenant_a)
        first = (await s.execute(text(
            "SELECT id FROM customers"))).scalars().all()
        assert [str(i) for i in first] == [rls_world.customer_a]

    runtime.reset_db_scope()
    runtime._request_tenant_id.set(rls_world.tenant_b)
    async with runtime.SessionLocal() as s:
        _role, tenant = await _guc(s)
        assert tenant == rls_world.tenant_b, "the new request's scope must win"
        second = (await s.execute(text(
            "SELECT id FROM customers"))).scalars().all()
        assert [str(i) for i in second] == [rls_world.customer_b]

    runtime.reset_db_scope()
    async with runtime.SessionLocal() as s:
        _role, tenant = await _guc(s)
        assert tenant == "", "a scope-less request must not inherit the last one"
        assert (await s.execute(
            text("SELECT count(*) FROM customers"))).scalar_one() == 0


# ── savepoints ───────────────────────────────────────────────────────────────

async def test_a_savepoint_rollback_neither_clears_nor_widens_the_scope(runtime, rls_world):
    """``SET LOCAL`` is transactional, so a savepoint taken AFTER the scope was
    pinned cannot roll the scope away — and rolling back to it must not leave
    the transaction reading more than it did before."""
    async with runtime.SessionLocal() as s:
        await runtime.set_tenant_scope(s, rls_world.tenant_a)
        try:
            async with s.begin_nested():
                await s.execute(text("SELECT count(*) FROM customers"))
                raise RuntimeError("roll the savepoint back")
        except RuntimeError:
            pass
        role, tenant = await _guc(s)
        assert (role, tenant) == ("muddato_app", rls_world.tenant_a)
        rows = (await s.execute(text("SELECT id FROM customers"))).scalars().all()
        assert [str(i) for i in rows] == [rls_world.customer_a]


# ── malformed input fails closed, not loudly ─────────────────────────────────

async def test_a_malformed_tenant_id_reads_nothing_and_does_not_raise(runtime, rls_world):
    """Two layers agree on this: ``_canonical_uuid`` normalises junk to blank
    before it can reach the GUC, and the SQL accessor's regex returns NULL for
    anything that is not a canonical UUID. Neither produces a 22P02, which the
    API would have surfaced as an unexplained 500."""
    async with runtime.SessionLocal() as s:
        await runtime.set_tenant_scope(s, "not-a-uuid")
        _role, tenant = await _guc(s)
        assert tenant == ""
        assert (await s.execute(
            text("SELECT count(*) FROM customers"))).scalar_one() == 0

    async with runtime.SessionLocal() as s:
        await s.execute(text(
            "SELECT set_config('app.tenant_id', '11111111-oops', true)"))
        assert (await s.execute(
            text("SELECT count(*) FROM customers"))).scalar_one() == 0


# ── authority does not leak between units of work ────────────────────────────

async def test_a_worker_session_does_not_leave_its_authority_behind(runtime, rls_world):
    """A scheduled job and a request handler can share a process (RUN_SCHEDULER
    is on in the web process), so the worker authority has to be scoped to the
    block that asked for it."""
    async with runtime.worker_session() as s:
        role, _tenant = await _guc(s)
        assert role == "muddato_worker"
        assert (await s.execute(
            text("SELECT count(*) FROM customers"))).scalar_one() == 2

    async with runtime.SessionLocal() as s:
        role, tenant = await _guc(s)
        assert (role, tenant) == ("muddato_app", "")
        assert (await s.execute(
            text("SELECT count(*) FROM customers"))).scalar_one() == 0


async def test_an_elevated_request_does_not_leave_platform_authority_behind(
    runtime, rls_world,
):
    async with runtime.SessionLocal() as s:
        await runtime.use_authority(s, runtime.DbAuthority.platform)
        role, _t = await _guc(s)
        assert role == "muddato_platform"
    runtime.reset_db_scope()
    async with runtime.SessionLocal() as s:
        role, _t = await _guc(s)
        assert role == "muddato_app"


async def test_customer_authority_is_keyed_on_the_identity_not_the_role(
    runtime, rls_world,
):
    """Assuming the customer role is not itself an authorization: without a
    verified identity in ``app.user_id`` it reads nothing."""
    async with runtime.SessionLocal() as s:
        await runtime.use_authority(s, runtime.DbAuthority.customer)
        assert (await s.execute(
            text("SELECT count(*) FROM customers"))).scalar_one() == 0
        await runtime.set_identity_scope(s, rls_world.unverified_user)
        assert (await s.execute(
            text("SELECT count(*) FROM customers"))).scalar_one() == 0
        await runtime.set_identity_scope(s, rls_world.customer_user)
        assert (await s.execute(
            text("SELECT count(*) FROM customers"))).scalar_one() == 1
    runtime.reset_db_scope()


# ── capability scoping only ever narrows ─────────────────────────────────────

async def test_scope_to_object_never_repoints_an_existing_tenant_scope(
    runtime, rls_world,
):
    """A capability path resolves an object's tenant and pins it. A request that
    already HAS a tenant must keep it — re-pointing would let a tenant route
    walk into another shop by naming one of its objects."""
    from app.core.dbauth import scope_to_object

    async with runtime.SessionLocal() as s:
        await runtime.set_tenant_scope(s, rls_world.tenant_a)
        got = await scope_to_object(s, "installment", rls_world.installment_b)
        assert got is None
        _role, tenant = await _guc(s)
        assert tenant == rls_world.tenant_a
        assert (await s.execute(
            text("SELECT count(*) FROM installments"))).scalar_one() == 1
    runtime.reset_db_scope()


async def test_scope_to_object_narrows_an_unscoped_capability_request(
    runtime, rls_world,
):
    from app.core.dbauth import scope_to_object

    async with runtime.SessionLocal() as s:
        assert (await s.execute(
            text("SELECT count(*) FROM installments"))).scalar_one() == 0
        got = await scope_to_object(s, "installment", rls_world.installment_b)
        assert got == rls_world.tenant_b
        rows = (await s.execute(text("SELECT id FROM installments"))).scalars().all()
        assert [str(i) for i in rows] == [rls_world.installment_b]
    runtime.reset_db_scope()


async def test_an_unknown_object_grants_no_scope_at_all(runtime, rls_world):
    from uuid import uuid4

    from app.core.dbauth import scope_to_object

    async with runtime.SessionLocal() as s:
        assert await scope_to_object(s, "installment", uuid4()) is None
        _role, tenant = await _guc(s)
        assert tenant == ""
    runtime.reset_db_scope()
