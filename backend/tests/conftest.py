"""Test-wide environment bootstrap.

`app/config.py`'s ``Settings`` requires a few connection settings with no
defaults (DB/Redis URLs, Telegram bot token). Production and CI supply these via
the process environment or a repo-root ``.env``; the unit-level test suite never
opens a real socket (DB/Redis/Telegram are not touched), so we provide inert
dummy values here so ``Settings()`` can construct at import time.

``setdefault`` means an environment that already exports these — a developer's
shell or a CI job wired to an ephemeral Postgres (see #79) — wins; we only fill
the gaps. We deliberately do NOT set ``RUN_SCHEDULER`` so its default-True
behaviour stays under test (tests/test_worker.py).
"""
from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/muddato_test"
)
os.environ.setdefault(
    "DATABASE_URL_SYNC", "postgresql+psycopg://test:test@localhost:5432/muddato_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN")
os.environ.setdefault(
    "SECRET_KEY", "muddato-test-secret-key-32-bytes-minimum-only"
)

import pytest
import pytest_asyncio


@pytest.fixture(scope="session", autouse=True)
def isolate_external_http():
    """Block real Telegram calls and fail on leaked aiohttp sessions."""
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiohttp import ClientSession

    created: list[ClientSession] = []
    original_init = ClientSession.__init__

    def _track_session(self, *args, **kwargs) -> None:
        original_init(self, *args, **kwargs)
        created.append(self)

    async def _deny_telegram_request(*_args, **_kwargs):
        pytest.fail(
            "test attempted a real Telegram request; inject a fake bot or patch "
            "the application service boundary"
        )

    patches = pytest.MonkeyPatch()
    patches.setattr(ClientSession, "__init__", _track_session)
    patches.setattr(AiohttpSession, "make_request", _deny_telegram_request)
    yield
    patches.undo()

    leaked = [session for session in created if not session.closed]
    assert not leaked, f"tests leaked {len(leaked)} open aiohttp ClientSession object(s)"


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """Clear ``app.core.ratelimit`` counters before every test.

    The limiter is keyed ``rl:{bucket}:{identity}`` in the REAL Redis at
    REDIS_URL (see ``app/core/ratelimit.py``) — it is never mocked for the many
    HTTP-level tests that hit rate-limited routes directly (they build their own
    ASGI client rather than call ``get_redis()``). Several unrelated routes share
    a bucket (every public-form endpoint shares "public_form"), and the same
    test-client identity hits all of them, so hits accumulate across the WHOLE
    session — an earlier, unrelated test can push a later one over its limit
    purely by run order (24 backend tests were failing in CI this way: pydantic
    Settings guard tests were a separate bug, but every "unexpectedly 429'd" HTTP
    test traced back to this). Flushing the ``rl:*`` keyspace before each test
    keeps rate-limit state test-local, the same isolation the DB-backed tests
    already get from a fresh schema per test. Best-effort: if Redis is down the
    limiter itself fails open (see ``rate_limit()``), so a flush failure here
    must not fail the test — it would just mean that test also sees the
    fail-open path, which is already an exercised, correct behaviour.
    """
    import redis as redis_sync

    from app.config import settings

    try:
        client = redis_sync.Redis.from_url(settings.redis_url, socket_timeout=1)
        try:
            keys = client.keys("rl:*")
            if keys:
                client.delete(*keys)
        finally:
            client.close()
    except Exception:
        pass
    yield


@pytest_asyncio.fixture
async def async_client():
    """An httpx client bound to the ASGI app — no network, no lifespan, no DB.

    Suitable for HTTP auth-gate tests (#79): a protected route's auth dependency
    (app.core.security.current_user) raises 401 for a missing/garbage token
    *before* the endpoint runs any DB query, so these are fast and DB-free.
    """
    import httpx
    from httpx import ASGITransport

    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def _pg_test_url() -> str | None:
    """Async PG URL for the integration tier, or None to skip. Set in CI by the
    `integration` job (a postgres service); unset locally → tests skip."""
    import os as _os

    return _os.environ.get("TEST_DATABASE_URL")


@pytest_asyncio.fixture
async def migrated_db():
    """Async engine on TEST_DATABASE_URL with the REAL schema applied via
    ``alembic upgrade head`` — so the ``muddato_app`` role + RLS policies from
    migration r73 exist (the model-only ``isolation_ctx`` can't exercise those).

    Starts from a clean ``public`` schema each run (cluster roles persist; the
    migration is idempotent). Skips only when ``TEST_DATABASE_URL`` is unset; once
    it is set, setup failure is a test failure (QA-R-002).
    """
    url = _pg_test_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import settings
    from tests._schema_lock import reset_public_schema, run_locked_migration_sync

    # (QA-R-002) Once TEST_DATABASE_URL is set the DB tier is mandatory. A refused
    # connection, a privilege error, an invalid schema or a failed migration must
    # FAIL the run — converting them to a skip made a dead tier report green.
    #
    # (MUDD-95) `reset_public_schema` takes the same advisory lock
    # `run_locked_migration_sync` does below, so this DROP/CREATE can never
    # interleave with a migration's DDL — see `tests/_schema_lock.py`.
    #
    # env.py targets settings.migration_url_sync — the SCHEMA OWNER, which is
    # the only identity that can run DDL. Outside production that resolves to
    # DATABASE_URL_SYNC when no migration URL is configured, so a one-URL
    # developer database still migrates; the integration job sets both and
    # exercises the real split. The advisory lock is taken on the same
    # identity that performs the DDL.
    engine = create_async_engine(url)
    await reset_public_schema(engine, settings.migration_url_sync)
    await asyncio.to_thread(run_locked_migration_sync, settings.migration_url_sync)
    try:
        yield engine
    finally:
        # Leave a clean, empty public schema so a following fixture (e.g. the
        # model-only isolation_ctx) isn't tripped by migration-built constraints
        # its metadata.drop_all can't order. Cluster roles persist (idempotent).
        try:
            await reset_public_schema(engine, settings.migration_url_sync)
        finally:
            await engine.dispose()


@pytest_asyncio.fixture
async def isolation_ctx():
    """Ephemeral schema on TEST_DATABASE_URL with a get_db override and two seeded
    tenants (A, B) — each an owner user + one customer. Yields (client, ctx) where
    ctx carries the seeded ids and an auth header for user A. Skips only when
    ``TEST_DATABASE_URL`` is unset (the integration tier runs in CI); once it is
    set, setup failure is a test failure (QA-R-002). (#79)
    """
    url = _pg_test_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Customer, Tenant, TenantUser, User
    from app.models.enums import TenantRole
    from tests._schema_lock import reset_public_schema, schema_lock

    engine = create_async_engine(url)
    # (QA-R-002) DB tier is mandatory once TEST_DATABASE_URL is set — setup errors
    # (refused connection, privilege, bad schema) must FAIL, never self-skip green.
    #
    # `Base.metadata.drop_all()` orders drops from SQLAlchemy's FK graph alone —
    # it has no idea an RLS POLICY on `users`/`tenants`/`tenant_settings` also
    # reads `tenant_users` (rf0failclosed / r94childrls / r99globalrls), which
    # Postgres tracks as a real dependency. Once anything in the run (this job's
    # own `alembic upgrade head` provisioning step, or an earlier `migrated_db`
    # test) has applied those migrations to this same database, drop_all tries to
    # drop `tenant_users` while a surviving policy still depends on it and fails
    # with DependentObjectsStillExistError — 100% reproducible, not flaky.
    # `DROP SCHEMA ... CASCADE` removes tables AND the policies/dependencies on
    # them in one statement, so ordering is never in question. Mirrors the same
    # fix already applied to `migrated_db`'s teardown below.
    #
    # (MUDD-95) `schema_lock` is the same advisory lock `migrated_db` /
    # `rls_world` hold — this DROP/CREATE + create_all can no longer
    # interleave with either's DDL. See `tests/_schema_lock.py`.
    async with schema_lock(settings.migration_url_sync), engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    # (Batch 5 independent-review correction) Also rebind the module-level
    # session factory, not just the FastAPI dependency.
    #
    # Code that runs outside a request — the bot's `/start`, the scheduler, any
    # `worker_session()` — reads `app.db.SessionLocal`, which the dependency
    # override does not touch. That used to be invisible: `DATABASE_URL` was the
    # schema owner and usually pointed at this same database, so the call
    # happened to work. It is now the restricted application login, which has no
    # privilege on a MODEL-ONLY schema (this fixture builds tables with
    # `create_all`; there are no migrations here, so there are no grants and no
    # policies). Pointing both seams at this fixture's engine makes the test
    # exercise the database it set up, instead of whichever one the environment
    # happened to name.
    import app.db as _dbmod

    _previous_factory = _dbmod.SessionLocal
    _dbmod.SessionLocal = Session
    try:
        async with Session() as s:
            ta, tb = Tenant(slug="tenant-a", name="Tenant A"), Tenant(slug="tenant-b", name="Tenant B")
            s.add_all([ta, tb])
            await s.flush()
            ua, ub = User(telegram_id=100001), User(telegram_id=100002)
            s.add_all([ua, ub])
            await s.flush()
            ca = Customer(tenant_id=ta.id, full_name="Alice A", phone="+998900000001")
            cb = Customer(tenant_id=tb.id, full_name="Bob B", phone="+998900000002")
            s.add_all([
                TenantUser(tenant_id=ta.id, user_id=ua.id, role=TenantRole.owner),
                TenantUser(tenant_id=tb.id, user_id=ub.id, role=TenantRole.owner),
                ca, cb,
            ])
            await s.flush()  # assigns ca.id / cb.id
            ctx = {
                "tenant_a": str(ta.id), "tenant_b": str(tb.id),
                "customer_a": str(ca.id), "customer_b": str(cb.id),
                "auth_a": {"Authorization": f"Bearer {issue_token(user_id=str(ua.id))}"},
                # (Phase 3) Additive: the second tenant's owner and both users'
                # Telegram ids, so a test can assert that linking a customer to a
                # Telegram account grants THAT customer's contracts and no others
                # — the isolation guarantee behind removing the join gate.
                "auth_b": {"Authorization": f"Bearer {issue_token(user_id=str(ub.id))}"},
                "user_a": str(ua.id), "user_b": str(ub.id),
                "tg_a": ua.telegram_id, "tg_b": ub.telegram_id,
            }
            await s.commit()

        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, ctx
    finally:
        app.dependency_overrides.pop(get_db, None)
        _dbmod.SessionLocal = _previous_factory
        await reset_public_schema(engine, settings.migration_url_sync)
        await engine.dispose()


@pytest.fixture
def grant_verified_phone():
    """Stamp the phone proof the customer API requires, on a test user.

    `/me` reads are gated on proved ownership: a `customers.telegram_id` link is
    a pointer, and links exist that predate any proof. Fixtures that act as a
    CUSTOMER therefore have to establish one, exactly as the bot would.

    Not a shortcut around the gate — it writes the same four columns the contact
    handler writes, so the database CHECK constraints still apply.
    """
    return _grant_verified_phone


def _grant_verified_phone(user, phone: str | None = None) -> None:
    from datetime import UTC, datetime

    from app.core.phone_identity import classify

    number = phone or f"+99890{user.telegram_id % 10_000_000:07d}"
    canonical, eligibility = classify(number)
    user.verified_phone = canonical
    user.verified_phone_at = datetime.now(UTC)
    user.verified_phone_source = "telegram_contact"
    user.phone_eligibility = eligibility.value


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def rls_world():
    """The seeded two-tenant world on the REAL migrated schema (Batch 5).

    Module-scoped because migrations are not cheap, and the event loop is
    module-scoped with it: an asyncpg connection belongs to the loop that opened
    it, so handing a module-scoped engine to function-scoped loops fails with
    "attached to a different loop". Modules using this declare
    ``pytestmark = pytest.mark.asyncio(loop_scope="module")``.

    Teardown resets the schema, exactly as ``migrated_db`` does. It has to: the
    fail-closed policies reference OTHER tables (``users`` is filtered through
    ``tenant_users``), so a following model-only fixture's ``drop_all`` cannot
    order the drops and fails with DependentObjectsStillExist.
    """
    from tests import _rls_world as world_mod

    url = world_mod.pg_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the RLS tier needs real Postgres")
    world = await world_mod.build(url)
    try:
        yield world
    finally:
        # (MUDD-95) Same shared advisory lock as migrated_db / isolation_ctx —
        # see tests/_schema_lock.py.
        from app.config import settings
        from tests._schema_lock import reset_public_schema

        try:
            await reset_public_schema(world.engine, settings.migration_url_sync)
        finally:
            await world.engine.dispose()
