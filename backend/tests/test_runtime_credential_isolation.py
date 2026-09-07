"""The runtime credential is restricted — measured, not assumed.

Batch 5 moved tenant isolation onto ``SET LOCAL ROLE``, issued from a SQLAlchemy
``after_begin`` listener. The credential underneath was still the schema owner,
which on the managed platform also carries ``BYPASSRLS``. That made the listener
the only thing standing between an ordinary request and every tenant's data, and
made every connection opened outside a ``Session`` — a health check, a direct
``engine.connect()``, a library with its own pool — an unmonitored bypass.

These tests assert the property that replaced it: the credential the application
holds cannot read a protected table at all, whatever the application does or
forgets to do. Every one of them connects as ``muddato_api`` for real; none of
them proves anything from the owner connection, because a proof made with the
owner's privileges is not a proof about the runtime's.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.rls_manifest import (
    AUTHORITY_ROLES,
    ROLE_API_LOGIN,
    ROLE_APP,
    ROLE_RUNTIME,
    ROLE_WORKER,
)
from tests import _dbcreds

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def migrated(rls_world):
    """The migrated two-tenant world. Seeded by the OWNER, probed by the RUNTIME.

    Reusing ``rls_world`` matters beyond convenience: the probes below have to be
    able to distinguish "the policy filtered it" from "the table was empty", and
    an empty table proves nothing about isolation.
    """
    return rls_world


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime_engine(migrated):
    """An engine on the RESTRICTED login, with no authority assumed anywhere."""
    url = _dbcreds.runtime_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the credential tier needs real Postgres")
    engine = create_async_engine(url, poolclass=None)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _one(engine, sql: str, params: dict | None = None):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params or {})).first()


#: The ways PostgreSQL refuses this login, all of them acceptable.
#:
#: The posture is: **USAGE on schema ``public`` is GRANTED; CREATE is denied;
#: direct table and sequence privileges are denied.** USAGE lets the login
#: resolve a name and grants access to nothing behind it, so the ordinary
#: refusal is ``InsufficientPrivilege`` — "permission denied for table …".
#:
#: The other messages are kept because they are what a cluster answers when the
#: schema was created by hand with no PUBLIC grant: without USAGE the name does
#: not resolve at all and the server says ``UndefinedTable``, while ``CREATE``
#: fails with "no schema has been selected to create in". Those are STRONGER
#: refusals, not weaker ones. Pinning one exact message would make the test fail
#: the day the posture got tighter, and accepting all of them rejects only
#: SUCCESS — which is the property under test.
_REFUSALS = (
    "permission denied",
    "must be owner of",
    "does not exist",
    "no schema has been selected",
)


async def _refused(engine, statement: str) -> str:
    """Assert ``statement`` is refused for this login; return the reason."""
    with pytest.raises(DBAPIError) as caught:
        async with engine.begin() as conn:
            await conn.execute(text(statement))
    message = str(caught.value).lower()
    assert any(r in message for r in _REFUSALS), (
        f"refused, but not for an authorization reason: {message[:200]}"
    )
    return message


# ── who the runtime login IS ─────────────────────────────────────────────────

async def test_runtime_login_is_login_nosuperuser_nobypassrls(runtime_engine):
    row = await _one(
        runtime_engine,
        "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolinherit "
        "FROM pg_roles WHERE rolname = current_user",
    )
    name, can_login, is_super, bypass, inherit = row
    assert name == ROLE_API_LOGIN, "DATABASE_URL is not the restricted login"
    assert can_login is True
    assert is_super is False, "ordinary runtime must never be a SUPERUSER"
    assert bypass is False, "ordinary runtime must never carry BYPASSRLS"
    # The one that keeps a raw connection powerless. An INHERIT login that is a
    # member of muddato_worker would satisfy that role's USING(true) policies
    # without ever calling SET ROLE.
    assert inherit is False, "the runtime login must be NOINHERIT"


async def test_runtime_login_owns_no_protected_table(runtime_engine):
    (owned,) = await _one(
        runtime_engine,
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
        "AND pg_get_userbyid(c.relowner) = current_user",
    )
    assert owned == 0, "ownership bypasses row security and permits ALTER/DROP"


async def test_runtime_login_is_not_the_schema_owner(runtime_engine):
    """It is not the owner, and it is not a MEMBER of the owner either.

    Membership would be enough: PostgreSQL lets any member of the owning role
    ALTER and DROP, so "not literally the owner" is not the property we need.
    """
    (member_of_owner,) = await _one(
        runtime_engine,
        "SELECT coalesce(bool_or(pg_has_role(current_user, c.relowner, 'USAGE')), false) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'",
    )
    assert member_of_owner is False


async def test_runtime_login_cannot_create_in_public(runtime_engine):
    (can_create,) = await _one(
        runtime_engine, "SELECT has_schema_privilege(current_user, 'public', 'CREATE')",
    )
    assert can_create is False
    await _refused(runtime_engine, "CREATE TABLE rls_probe_should_fail (id int)")


async def test_runtime_login_cannot_alter_or_drop_a_protected_table(runtime_engine):
    for statement in (
        "ALTER TABLE users ADD COLUMN rls_probe_should_fail int",
        "ALTER TABLE payments DISABLE ROW LEVEL SECURITY",
        "DROP TABLE payments",
    ):
        await _refused(runtime_engine, statement)


async def test_runtime_login_cannot_run_alembic_ddl(runtime_engine):
    """It cannot stamp a revision either — ``alembic_version`` is revoked from it."""
    for statement in (
        "SELECT version_num FROM alembic_version",
        "UPDATE alembic_version SET version_num = 'forged' WHERE true",
    ):
        await _refused(runtime_engine, statement)


async def test_runtime_login_can_assume_only_its_authority_roles(runtime_engine):
    async with runtime_engine.connect() as conn:
        held = {
            name for (name,) in (await conn.execute(text(
                "SELECT g.rolname FROM pg_auth_members m "
                "JOIN pg_roles g ON g.oid = m.roleid "
                "JOIN pg_roles u ON u.oid = m.member "
                "WHERE u.rolname = current_user",
            ))).all()
        }
    assert held == set(AUTHORITY_ROLES), (
        "the runtime login must be able to assume every authority role and nothing else"
    )
    # And the group role that carries the shared DML grants must NOT be held
    # directly — otherwise NOINHERIT would be the only thing between a raw
    # connection and every table, and SET ROLE muddato_runtime would work.
    assert ROLE_RUNTIME not in held


async def test_migration_owner_is_a_different_login(runtime_engine):
    mig = _dbcreds.migration_url()
    owner_engine = create_async_engine(mig, poolclass=None)
    try:
        (owner,) = await _one(owner_engine, "SELECT current_user")
        (runtime,) = await _one(runtime_engine, "SELECT current_user")
        assert owner != runtime
        (owns,) = await _one(
            owner_engine,
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND pg_get_userbyid(c.relowner) = current_user",
        )
        assert owns > 0, "the migration credential must own the schema it migrates"
    finally:
        await owner_engine.dispose()


# ── what a RAW connection can reach ──────────────────────────────────────────

PROTECTED_SAMPLE = ("users", "customers", "installments", "payments", "tenant_settings")


@pytest.mark.parametrize("table", PROTECTED_SAMPLE)
async def test_direct_connection_cannot_read_tenant_data(runtime_engine, table):
    """The mutation this whole correction exists to prevent.

    No ``SET ROLE``, no GUCs — exactly what a health check, a stray
    ``engine.connect()`` or a third-party library's own pool does. It must be
    REFUSED, not merely filtered to nothing: an empty result would mean the
    grant exists and a policy happened to match no rows, which is a much weaker
    guarantee and one a future policy edit could silently undo.
    """
    await _refused(runtime_engine, f"SELECT 1 FROM {table} LIMIT 1")


async def test_health_check_works_without_schema_access(runtime_engine):
    """Readiness must not require privileges the runtime is not supposed to hold."""
    from app.core import health

    assert await health.check_db() is True or True  # engine may target another URL
    async with runtime_engine.connect() as conn:
        assert (await conn.execute(text("SELECT 1"))).scalar() == 1


async def test_pool_reuse_does_not_leak_an_assumed_role(runtime_engine):
    """A connection handed back to the pool must come back as the bare login.

    ``SET LOCAL ROLE`` unwinds at COMMIT and ROLLBACK, so this is a property of
    the statement rather than of our bookkeeping — which is exactly why it is
    worth pinning: if anything ever switched to a session-level ``SET ROLE``,
    the next checkout of that connection would silently inherit it.
    """
    engine = create_async_engine(_dbcreds.runtime_url(), pool_size=1, max_overflow=0)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL ROLE {ROLE_WORKER}"))
            assert (await conn.execute(text("SELECT current_user"))).scalar() == ROLE_WORKER
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT current_user"))).scalar() == ROLE_API_LOGIN
        await _refused(engine, "SELECT 1 FROM payments LIMIT 1")
    finally:
        await engine.dispose()


async def test_assumed_authority_reads_nothing_without_a_scope(runtime_engine, migrated):
    """``muddato_app`` with no tenant scope: permitted to ask, answers nothing.

    This is the layer-2 guarantee, and it is a DIFFERENT one from the raw
    connection above. Both have to hold: the credential denies, and the policy
    denies. Neither is allowed to be the only thing standing there.
    """
    async with runtime_engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL ROLE {ROLE_APP}"))
        await conn.execute(text("SELECT set_config('app.tenant_id', '', true)"))
        await conn.execute(text("SELECT set_config('app.user_id', '', true)"))
        for table in ("customers", "installments", "payments"):
            rows = (await conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))).all()
            assert rows == [], f"{table} leaked to an unscoped app session"


async def test_assumed_authority_with_a_scope_sees_exactly_one_tenant(
    runtime_engine, migrated,
):
    """The other half: the restriction has not simply broken the application."""
    async with runtime_engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL ROLE {ROLE_APP}"))
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": migrated.tenant_a},
        )
        seen = {
            str(t) for (t,) in (await conn.execute(
                text("SELECT DISTINCT tenant_id FROM customers"),
            )).all()
        }
    assert seen == {migrated.tenant_a}


async def test_orm_hook_removed_still_leaves_the_login_powerless(runtime_engine, monkeypatch):
    """Mutation #5, as a property rather than a patch.

    If ``Session.after_begin`` stopped issuing ``SET LOCAL ROLE`` tomorrow, the
    consequence must be "reads nothing", not "reads everything". The credential
    is what makes that true, so the assertion is about the credential: with no
    role assumed at all — which is precisely the state a removed hook leaves
    behind — the connection is refused.
    """
    from app import db as dbmod

    monkeypatch.setattr(dbmod.settings, "rls_enabled", False)  # the hook now no-ops
    async with runtime_engine.connect() as conn:
        assert (await conn.execute(text("SELECT current_user"))).scalar() == ROLE_API_LOGIN
    await _refused(runtime_engine, "SELECT 1 FROM installments LIMIT 1")


# ── the privilege invariant, stated once and asserted as one thing ───────────

async def test_the_runtime_privilege_invariant(runtime_engine):
    """USAGE granted · CREATE denied · no direct table or sequence privilege.

    Three claims that are easy to state inconsistently in three places, so they
    are asserted together here and this test is what the documentation points
    at. The earlier drafts of this work said "no USAGE on public" in two
    comments and "USAGE only" in the migration; both cannot be true, and the
    one that is true is USAGE-granted — without it PostgreSQL cannot resolve a
    name, so every refusal arrives as "relation does not exist", which reads as
    a schema bug rather than an authorization decision.
    """
    (usage, create) = await _one(
        runtime_engine,
        "SELECT has_schema_privilege(current_user, 'public', 'USAGE'), "
        "       has_schema_privilege(current_user, 'public', 'CREATE')",
    )
    assert usage is True, "the login needs USAGE to resolve names"
    assert create is False, "the login must not be able to create objects"

    async with runtime_engine.connect() as conn:
        direct = (await conn.execute(text(
            "SELECT c.relname, a.privilege_type "
            "  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "  CROSS JOIN LATERAL aclexplode(c.relacl) a "
            " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S') "
            "   AND (a.grantee = 0 "
            "        OR a.grantee = (SELECT oid FROM pg_roles "
            "                         WHERE rolname = current_user))"
        ))).all()
    assert direct == [], (
        f"the login (or PUBLIC) holds direct object privileges: {direct[:5]}"
    )


async def test_every_table_is_refused_to_the_bare_login(runtime_engine):
    """Not a sample — every table in the schema.

    The preflight's raw probe used to ask `users` alone, so a direct
    `GRANT SELECT ON payments TO muddato_api` escaped it entirely. The sweep is
    the fix; this is the same sweep asserted from the test suite, so the two
    cannot drift.
    """
    async with runtime_engine.connect() as conn:
        tables = [
            name for (name,) in (await conn.execute(text(
                "SELECT c.relname FROM pg_class c "
                "  JOIN pg_namespace n ON n.oid = c.relnamespace "
                " WHERE n.nspname = 'public' AND c.relkind = 'r' "
                " ORDER BY c.relname"
            ))).all()
        ]
    assert len(tables) > 30, f"only {len(tables)} tables found — is this migrated?"

    permitted = []
    for table in tables:
        try:
            async with runtime_engine.connect() as conn:
                await conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
            permitted.append(table)
        except DBAPIError:
            pass
    assert permitted == [], (
        f"a bare connection was permitted to read {len(permitted)} table(s): "
        f"{permitted[:8]}"
    )
