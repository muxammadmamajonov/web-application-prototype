"""Sixteen mutations, each proving one guard actually bites.

A passing security test proves nothing on its own: it might be passing because
the property holds, or because the assertion cannot fail. Every guard this
correction adds is therefore broken here, on purpose, and the test asserts the
guard NOTICES. If a mutation stops turning its assertion red, that guard has
become decorative and this file will say so.

Two kinds of mutation appear, and the difference is stated at each one:

* **live** — the real object is mutated (a ``GRANT``, a policy, an
  ``ALTER FUNCTION``, a re-attached router dependency) and reverted in a
  ``finally``;
* **judgement** — a fact that only a superuser can create (``ALTER ROLE …
  BYPASSRLS``, re-owning a table) is fed to the judgement code directly. The
  assertion under test is the same one; what is simulated is the cluster state,
  not the check. Where a superuser URL is available via
  ``TEST_SUPERUSER_DATABASE_URL`` the live form runs as well.

Nothing here writes to a table that outlives the test: DDL is reverted, DML runs
in a rolled-back transaction, and the two roles involved are test-cluster roles.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.cli import rls_preflight as pf
from app.core.rls_manifest import ROLE_API_LOGIN, ROLE_IDENTITY, ROLE_RUNTIME
from tests import _dbcreds

#: The environment a boot-guard proof is allowed to see.
#:
#: pydantic-settings reads ``os.environ``, so a developer shell (or this suite's
#: own DB variables) would otherwise supply the very URL a test is asserting is
#: ABSENT — and "production refuses to boot without a migration credential"
#: would pass or fail depending on whose laptop ran it. Every ``Settings``
#: construction below happens inside a cleared environment.
_CLEAN_ENV: dict[str, str] = {}


@pytest.fixture(scope="module", autouse=True)
def world():
    """A migrated, SEEDED schema for the whole module.

    Not optional and not shared: the DDL mutations below need real tables, and
    ``_identity_reads_users`` has to be able to tell "the policy filtered it"
    from "the table was empty" — an empty table would make every read-denial
    assertion pass for the wrong reason.

    Built synchronously (these are sync tests) and torn down the same way the
    ``rls_world`` fixture does, so a following model-only fixture is not left
    tripping over migration-built constraints.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests import _rls_world as world_mod

    url = world_mod.pg_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — mutation proofs need real Postgres")
    async def _build():
        # Build AND dispose inside ONE loop. `build` opens asyncpg connections,
        # and an asyncpg connection belongs to the loop that created it —
        # disposing the engine from a second `asyncio.run` leaves those
        # connections attached to a loop that is already closed, which surfaces
        # much later as "Event loop is closed" inside an unrelated module.
        world = await world_mod.build(url)
        await world.engine.dispose()
        return world

    built = asyncio.run(_build())
    try:
        yield built
    finally:
        async def _reset() -> None:
            engine = create_async_engine(url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                    await conn.execute(text("CREATE SCHEMA public"))
            finally:
                await engine.dispose()

        asyncio.run(_reset())


def _skip_without_db() -> tuple[str, str]:
    owner = _dbcreds.migration_url()
    if not owner:
        pytest.skip("TEST_DATABASE_URL not set — mutation proofs need real Postgres")
    return _dbcreds.sync(owner), _dbcreds.sync(_dbcreds.runtime_url())


def _owner_exec(statements: tuple[str, ...]) -> None:
    """Run DDL as the schema owner, each statement autocommitted."""
    owner_url, _ = _skip_without_db()
    engine = create_engine(owner_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
    finally:
        engine.dispose()


def _runtime_report() -> pf.Report:
    """A freshly collected application posture, unjudged."""
    _, runtime_url = _skip_without_db()
    engine = create_engine(runtime_url)
    try:
        with engine.connect() as conn:
            report = pf.collect(
                conn, url=runtime_url, expected_runtime_role=ROLE_API_LOGIN,
            )
            conn.rollback()
    finally:
        engine.dispose()
    return report


def _judged(report: pf.Report) -> list[str]:
    report.failures = []
    pf._judge(report)
    return report.failures


def _judged_whole(report: pf.Report) -> list[str]:
    """Judge with the MIGRATION facts filled in, so "no failures" means what it says.

    ``collect`` only sees the application connection. The migration identity,
    the live Alembic head and the logical-database comparison are gathered on
    the OTHER connection by ``collect_migration``, and judging a runtime-only
    report would report them all as "not established" — true, and pure noise in
    a test whose subject is something else.

    They are COLLECTED for real rather than stubbed. Stubbing
    ``same_database = True`` would have made mutation 32 — the whole point of
    which is that the two connections address different databases — pass
    against a stub of itself.
    """
    owner_url, _ = _skip_without_db()
    engine = create_engine(owner_url)
    try:
        with engine.connect() as conn:
            pf.collect_migration(
                conn, report, url=owner_url, runtime_user=report.current_user,
            )
    finally:
        engine.dispose()
    pf.check_webhook_routes(report)
    return _judged(report)


def _prod_settings(**over: object):
    """Build a production ``Settings`` with the given overrides.

    Constructed from an explicit dict rather than the ambient environment, so a
    developer's ``.env`` cannot make a boot-guard proof pass or fail by accident.
    """
    from app.config import Settings

    base = {
        "environment": "production",
        "secret_key": "x" * 48,
        "risk_hash_salt": "not-the-dev-default",
        "allowed_origins": "https://muddato.uz",
        "redis_url": "redis://localhost:6379/0",
        "telegram_bot_token": "1:abc",
        "database_url": "postgresql+asyncpg://muddato_api@db/muddato",
        "database_url_sync": "postgresql+psycopg://muddato_api@db/muddato",
        "migration_database_url_sync": "postgresql+psycopg://owner@db/muddato",
    }
    base.update(over)
    base = {k: v for k, v in base.items() if v is not None}
    with patch.dict(os.environ, _CLEAN_ENV, clear=True):
        return Settings(_env_file=None, **base)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════
# 1 · Runtime DATABASE_URL changed back to the schema owner
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_01_runtime_url_is_the_schema_owner():
    """LIVE (config). Production must refuse to boot when the two URLs are one."""
    healthy = _prod_settings()
    assert healthy.migration_credential_is_separate is True

    with pytest.raises(ValueError, match="SAME role"):
        _prod_settings(
            database_url="postgresql+asyncpg://owner@db/muddato",
            database_url_sync="postgresql+psycopg://owner@db/muddato",
        )


def test_mutation_01b_preflight_rejects_a_shared_login():
    """LIVE (preflight). The same mutation, caught at release time."""
    report = _runtime_report()
    report.migration_checked = True
    report.migration_owns_tables = 48
    report.migration_user = report.current_user      # the mutation
    report.migration_is_separate = False
    report.alembic_head = report.expected_head
    assert any("SAME" in f for f in _judged(report)), (
        "the preflight accepted one credential doing both jobs"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 2 · Runtime login granted BYPASSRLS
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_02_runtime_login_has_bypassrls():
    """JUDGEMENT — ``ALTER ROLE … BYPASSRLS`` needs a superuser, which the
    migration identity deliberately is not. The fact is injected; the assertion
    is the real one, and it was previously a *recorded risk* rather than a
    failure, which is exactly the finding this corrects."""
    report = _runtime_report()
    baseline = _judged(report)
    assert not any("BYPASSRLS" in f for f in baseline), baseline

    report.rolbypassrls = True                        # the mutation
    failures = _judged(report)
    assert any("BYPASSRLS" in f for f in failures), (
        "a BYPASSRLS runtime connection is still tolerated"
    )


def test_mutation_02b_live_bypassrls_when_a_superuser_is_available():
    """LIVE, when the environment can create the state at all."""
    su = os.environ.get("TEST_SUPERUSER_DATABASE_URL")
    if not su:
        pytest.skip("TEST_SUPERUSER_DATABASE_URL not set — see the judgement form above")
    engine = create_engine(_dbcreds.sync(su), isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"ALTER ROLE {ROLE_API_LOGIN} BYPASSRLS"))
        assert any("BYPASSRLS" in f for f in _judged(_runtime_report()))
    finally:
        with engine.connect() as conn:
            conn.execute(text(f"ALTER ROLE {ROLE_API_LOGIN} NOBYPASSRLS"))
        engine.dispose()
    assert not any("BYPASSRLS" in f for f in _judged(_runtime_report()))


# ═══════════════════════════════════════════════════════════════════════════
# 3 · Runtime login becomes a protected-table owner
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_03_runtime_login_owns_a_table():
    """JUDGEMENT — re-owning a table requires membership in the target role,
    which the schema owner deliberately does not hold."""
    report = _runtime_report()
    assert report.owned_tables == []
    report.owned_tables = ["payments"]                # the mutation
    report.owns_protected_tables = 1
    assert any("OWNS" in f for f in _judged(report)), (
        "an owning runtime login is not treated as a failure"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 4 · Runtime login receives CREATE on public
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_04_runtime_login_can_create_in_public():
    """LIVE. The schema owner can grant this, so the real mutation is possible."""
    assert not any("CREATE in schema public" in f for f in _judged(_runtime_report()))
    try:
        _owner_exec((f"GRANT CREATE ON SCHEMA public TO {ROLE_API_LOGIN}",))
        failures = _judged(_runtime_report())
        assert any("CREATE in schema public" in f for f in failures), (
            "a runtime login that can create objects is not flagged"
        )
    finally:
        _owner_exec((f"REVOKE CREATE ON SCHEMA public FROM {ROLE_API_LOGIN}",))
    assert not any("CREATE in schema public" in f for f in _judged(_runtime_report()))


# ═══════════════════════════════════════════════════════════════════════════
# 5 · The session downgrade hook is removed
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_05_downgrade_hook_removed_is_survivable():
    """LIVE, and the most important one.

    Removing ``Session.after_begin``'s ``SET LOCAL ROLE`` used to mean every
    transaction ran as the owner. The mutation is simulated exactly — no role is
    assumed at all — and the two credentials are compared side by side. The
    OWNER reads rows (which is what the old design would have leaked); the
    RUNTIME is refused. That contrast IS the correction.
    """
    owner_url, runtime_url = _skip_without_db()

    owner = create_engine(owner_url)
    try:
        with owner.connect() as conn:
            leaked = conn.execute(text("SELECT count(*) FROM users")).scalar()
    finally:
        owner.dispose()
    assert leaked and leaked > 0, "the world is empty — this proves nothing"

    runtime = create_engine(runtime_url)
    try:
        with pytest.raises(Exception) as caught, runtime.connect() as conn:
            conn.execute(text("SELECT count(*) FROM users"))
    finally:
        runtime.dispose()
    message = str(caught.value).lower()
    assert "permission denied" in message or "does not exist" in message


# ═══════════════════════════════════════════════════════════════════════════
# 6 · A direct engine connection reads tenant data without the intended scope
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("table", ["customers", "installments", "payments"])
def test_mutation_06_direct_connection_read(table):
    """LIVE. Same shape as #5, per protected table, through the app's own engine
    configuration rather than a hand-built URL."""
    _, runtime_url = _skip_without_db()
    engine = create_engine(runtime_url)
    try:
        with pytest.raises(Exception) as caught, engine.connect() as conn:
            conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
    finally:
        engine.dispose()
    assert "permission denied" in str(caught.value).lower() or \
           "does not exist" in str(caught.value).lower()


# ═══════════════════════════════════════════════════════════════════════════
# 7 · A webhook receives worker authority at router entry
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_07_worker_authority_back_on_a_webhook_router():
    """LIVE. The dependency is re-attached to the real assembled application."""
    from fastapi import Depends
    from fastapi.routing import APIRoute

    from app.core.dbauth import worker_authority
    from app.main import app

    clean = pf.Report()
    pf.check_webhook_routes(clean)
    assert clean.webhook_routes_checked >= 3
    assert clean.webhook_routes_with_authority == []

    victim = next(
        r for r in app.routes
        if isinstance(r, APIRoute) and "/webhooks/click" in r.path
    )
    original = list(victim.dependencies)
    try:
        victim.dependencies = [*original, Depends(worker_authority)]  # the mutation
        victim.dependant = victim.dependant
        # FastAPI resolves `dependencies` into `dependant` at include time, so
        # the mutation has to be applied where the check actually reads it.
        from fastapi.dependencies.utils import get_dependant, get_parameterless_sub_dependant

        victim.dependant = get_dependant(path=victim.path_format, call=victim.endpoint)
        for depends in reversed([*original, Depends(worker_authority)]):
            victim.dependant.dependencies.insert(
                0, get_parameterless_sub_dependant(
                    depends=depends, path=victim.path_format,
                ),
            )
        mutated = pf.Report()
        pf.check_webhook_routes(mutated)
        assert mutated.webhook_routes_with_authority, (
            "a webhook router carrying worker authority is not detected"
        )
        assert any("worker_authority" in f for f in _judged(mutated))
    finally:
        victim.dependencies = original
        victim.dependant = get_dependant(path=victim.path_format, call=victim.endpoint)
        for depends in reversed(original):
            victim.dependant.dependencies.insert(
                0, get_parameterless_sub_dependant(
                    depends=depends, path=victim.path_format,
                ),
            )
    restored = pf.Report()
    pf.check_webhook_routes(restored)
    assert restored.webhook_routes_with_authority == []


# ═══════════════════════════════════════════════════════════════════════════
# 8 · An invalid webhook signature performs a global provider lookup
# ═══════════════════════════════════════════════════════════════════════════
#
# LIVE, in tests/test_webhook_authority_order.py::
#   test_signature_validation_precedes_any_ordinary_cross_tenant_query
# which records every statement the request issues and fails if a tenant table
# is touched before verification. Asserted here as a presence check so the proof
# cannot be deleted without this file noticing.

def test_mutation_08_global_lookup_before_verification_is_covered():
    from tests import test_webhook_authority_order as mod

    assert hasattr(mod, "test_signature_validation_precedes_any_ordinary_cross_tenant_query")


# ═══════════════════════════════════════════════════════════════════════════
# 9 · A valid webhook is not narrowed to its tenant after authentication
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_09_missing_post_verification_scoping_is_covered():
    from tests import test_webhook_authority_order as mod

    assert hasattr(mod, "test_removing_post_verification_scoping_is_caught")


# ═══════════════════════════════════════════════════════════════════════════
# 10 · The identity role receives SELECT on users
# ═══════════════════════════════════════════════════════════════════════════

def _identity_reads_users() -> int:
    _, runtime_url = _skip_without_db()
    engine = create_engine(runtime_url)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text(f"SET LOCAL ROLE {ROLE_IDENTITY}"))
                conn.execute(text("SELECT set_config('app.user_id', '', true)"))
                return len(conn.execute(text("SELECT id FROM users LIMIT 5")).all())
            finally:
                trans.rollback()
    finally:
        engine.dispose()


def test_mutation_10_identity_gets_select_on_users():
    """LIVE. The exact policy this correction dropped, put back."""
    assert _identity_reads_users() == 0
    try:
        _owner_exec((
            "CREATE POLICY mutation_probe_users_identity ON users "
            f"FOR SELECT TO {ROLE_IDENTITY} USING (true)",
        ))
        assert _identity_reads_users() > 0, (
            "restoring the directory-wide read policy did not change what the "
            "pre-auth authority can see — the test is not measuring the policy"
        )
        report = _runtime_report()
        assert any("identity" in f and "users" in f for f in _judged(report)), (
            "the preflight did not flag a users policy for the pre-auth authority"
        )
    finally:
        _owner_exec(("DROP POLICY IF EXISTS mutation_probe_users_identity ON users",))
    assert _identity_reads_users() == 0


# ═══════════════════════════════════════════════════════════════════════════
# 11 · The identity role receives UPDATE on users
# ═══════════════════════════════════════════════════════════════════════════

def _identity_updates_users() -> int:
    _, runtime_url = _skip_without_db()
    engine = create_engine(runtime_url)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text(f"SET LOCAL ROLE {ROLE_IDENTITY}"))
                conn.execute(text("SELECT set_config('app.user_id', '', true)"))
                return conn.execute(
                    text("UPDATE users SET last_seen_at = now() WHERE true"),
                ).rowcount
            except Exception:
                return -1
            finally:
                trans.rollback()
    finally:
        engine.dispose()


def test_mutation_11_identity_gets_update_on_users():
    """LIVE. Note the shape: with no policy the UPDATE succeeds and changes
    ZERO rows (``USING`` filters, it does not raise), so the assertion has to be
    about the rowcount. Asserting "it raises" would have passed before and after."""
    assert _identity_updates_users() == 0
    try:
        _owner_exec((
            "CREATE POLICY mutation_probe_users_identity_upd ON users "
            f"FOR UPDATE TO {ROLE_IDENTITY} USING (true) WITH CHECK (true)",
        ))
        assert _identity_updates_users() > 0, (
            "restoring the directory-wide write policy changed nothing — the "
            "test is not measuring the policy"
        )
    finally:
        _owner_exec((
            "DROP POLICY IF EXISTS mutation_probe_users_identity_upd ON users",
        ))
    assert _identity_updates_users() == 0


# ═══════════════════════════════════════════════════════════════════════════
# 12 · A pre-auth handler performs an arbitrary user lookup
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_12_arbitrary_user_lookup_in_a_pre_auth_handler(world):
    """LIVE. The bare ``select(User).where(User.id == <anything>)`` that
    ``/auth/debug`` used to run, re-executed under the authority a pre-auth
    handler actually holds. It must find nothing, so writing that code again is
    a bug that fails closed rather than a leak."""
    _, runtime_url = _skip_without_db()
    engine = create_engine(runtime_url)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text("SET LOCAL ROLE muddato_app"))
                conn.execute(text("SELECT set_config('app.user_id', '', true)"))
                conn.execute(text("SELECT set_config('app.tenant_id', '', true)"))
                rows = conn.execute(
                    text("SELECT id FROM users WHERE id = :u"),
                    {"u": world.owner_b},
                ).all()
            finally:
                trans.rollback()
    finally:
        engine.dispose()
    assert rows == [], "a pre-auth handler could look up an arbitrary account"


# ═══════════════════════════════════════════════════════════════════════════
# 13 · A SECURITY DEFINER function has an unsafe search_path
# ═══════════════════════════════════════════════════════════════════════════

_VICTIM_FN = "public.rls_identity_user_state(bigint)"


def test_mutation_13_unpinned_search_path():
    """LIVE. An unpinned ``search_path`` in a definer function lets a caller who
    can create objects shadow a table name inside the definer's body."""
    assert not any("search_path" in f for f in _judged(_runtime_report()))
    try:
        _owner_exec((f"ALTER FUNCTION {_VICTIM_FN} RESET search_path",))
        failures = _judged(_runtime_report())
        assert any("search_path is not pinned" in f for f in failures), (
            "an unpinned definer function is not flagged"
        )
    finally:
        _owner_exec((
            f"ALTER FUNCTION {_VICTIM_FN} SET search_path = pg_catalog, public",
        ))
    assert not any("search_path" in f for f in _judged(_runtime_report()))


# ═══════════════════════════════════════════════════════════════════════════
# 14 · PUBLIC receives EXECUTE on a capability function
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_14_public_execute_on_a_capability():
    """LIVE. Two separate holes are checked: EXECUTE to PUBLIC, and EXECUTE to
    the shared runtime group — the second is subtler and just as bad, because it
    would let every tenant authority call a webhook-only resolver."""
    assert not any("PUBLIC holds EXECUTE" in f for f in _judged(_runtime_report()))
    try:
        _owner_exec((f"GRANT EXECUTE ON FUNCTION {_VICTIM_FN} TO PUBLIC",))
        assert any(
            "PUBLIC holds EXECUTE" in f for f in _judged(_runtime_report())
        ), "PUBLIC EXECUTE on a capability function is not flagged"
    finally:
        _owner_exec((f"REVOKE ALL ON FUNCTION {_VICTIM_FN} FROM PUBLIC",))

    try:
        _owner_exec((f"GRANT EXECUTE ON FUNCTION {_VICTIM_FN} TO {ROLE_RUNTIME}",))
        assert any(
            "holds EXECUTE" in f for f in _judged(_runtime_report())
        ), "a capability reachable by every authority is not flagged"
    finally:
        _owner_exec((f"REVOKE ALL ON FUNCTION {_VICTIM_FN} FROM {ROLE_RUNTIME}",))
        _owner_exec((f"GRANT EXECUTE ON FUNCTION {_VICTIM_FN} TO {ROLE_IDENTITY}",))
    assert _judged_whole(_runtime_report()) == []


# ═══════════════════════════════════════════════════════════════════════════
# 15 · The migration credential silently falls back to the runtime credential
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_15_missing_migration_url_in_production():
    """LIVE (config). No fallback: production refuses to boot, and the resolver
    used by Alembic raises rather than quietly returning the runtime URL."""
    with pytest.raises(ValueError, match="MIGRATION_DATABASE_URL"):
        _prod_settings(migration_database_url_sync=None)

    # And the resolver Alembic actually calls, in isolation from the boot guard:
    # a settings object that passed validation and then lost its migration URL
    # (config drift, a dropped secret) must RAISE rather than hand Alembic the
    # restricted runtime credential and fail halfway through the DDL.
    prod_like = _prod_settings()
    object.__setattr__(prod_like, "migration_database_url_sync", None)
    object.__setattr__(prod_like, "migration_database_url", None)
    with pytest.raises(RuntimeError, match="no fallback"):
        _ = prod_like.migration_url_sync


def test_mutation_15b_development_fallback_is_loud_but_allowed():
    """The other half of the same rule: OUTSIDE production a single URL still
    works, so a developer's laptop is not broken by a production guarantee."""
    from app.config import Settings

    with patch.dict(os.environ, _CLEAN_ENV, clear=True):
        dev = Settings(
            _env_file=None, environment="development", secret_key="x" * 48,
            redis_url="redis://x", telegram_bot_token="1:a",
            database_url="postgresql+asyncpg://me@db/m",
            database_url_sync="postgresql+psycopg://me@db/m",
        )  # type: ignore[call-arg]
    assert dev.migration_url_sync == "postgresql+psycopg://me@db/m"
    assert dev.migration_credential_is_separate is False


# ═══════════════════════════════════════════════════════════════════════════
# 16 · The runtime credential silently falls back to the migration owner
# ═══════════════════════════════════════════════════════════════════════════

def test_mutation_16_runtime_url_has_no_fallback():
    """There is no code path that substitutes the migration owner for a missing
    ``DATABASE_URL``: the field is required, so the application cannot start at
    all rather than starting with too much privilege."""
    from pydantic import ValidationError

    from app.config import Settings

    with patch.dict(os.environ, _CLEAN_ENV, clear=True), pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            _env_file=None, environment="production", secret_key="x" * 48,
            redis_url="redis://x", telegram_bot_token="1:a",
            migration_database_url_sync="postgresql+psycopg://owner@db/m",
        )

    # And the preflight refuses a runtime connection that is not the expected
    # login, so pointing DATABASE_URL at the owner by hand fails the release.
    report = _runtime_report()
    report.expected_runtime_role = "muddato_api"
    report.current_user = "postgres"                  # the mutation
    assert any("expected" in f for f in _judged(report))


# ═══════════════════════════════════════════════════════════════════════════
# Baseline: with nothing mutated, the posture passes.
# ═══════════════════════════════════════════════════════════════════════════

def test_baseline_posture_is_clean():
    """Every mutation above reverts. This is what proves they did."""
    assert _judged_whole(_runtime_report()) == []


# ═══════════════════════════════════════════════════════════════════════════
# THE FINAL CORRECTION PASS · mutations 17–25
#
# The first sixteen prove the rg0 authorization boundary. These nine prove the
# three gates that stood between "materially sound" and "shippable": the
# expired dependency alarm, and — mostly — the cutover that could not be
# executed from the real pre-rg0 posture.
# ═══════════════════════════════════════════════════════════════════════════

_BOOTSTRAP = ["-m", "scripts.bootstrap_runtime_login"]


def _bootstrap_env(**over: str) -> dict[str, str]:
    from tests import _dbcreds

    env = {"MUDDATO_BOOTSTRAP_MIGRATION_URL": _dbcreds.sync(_dbcreds.migration_url())}
    env.update(over)
    return env


def _run_tool(argv: list[str], env: dict[str, str]):
    import subprocess
    import sys
    from pathlib import Path

    return subprocess.run(
        [sys.executable, *argv], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, **env},
    )


# ═══ 17 · bootstrap attempted through the RESTRICTED runtime credential ═════

def test_mutation_17_bootstrap_through_the_runtime_credential():
    """LIVE. The bootstrap must fail when pointed at the login it provisions.

    The tool exists to be run as the OWNER. Handing it the restricted credential
    is the natural operator slip — the two URLs sit next to each other in the
    runbook — and it must not half-succeed, because a partially-applied
    bootstrap leaves a login with attributes set and memberships missing.
    """
    from tests import _dbcreds

    _skip_without_db()
    healthy = _run_tool(_BOOTSTRAP, _bootstrap_env())
    assert healthy.returncode == 0, healthy.stdout + healthy.stderr

    broken = _run_tool(_BOOTSTRAP, _bootstrap_env(
        MUDDATO_BOOTSTRAP_MIGRATION_URL=_dbcreds.sync(_dbcreds.runtime_url()),
    ))
    assert broken.returncode != 0, (
        "the bootstrap ran to completion through the restricted login — it must "
        "require the owner"
    )

    # And the restore: the owner path still works afterwards.
    assert _run_tool(_BOOTSTRAP, _bootstrap_env()).returncode == 0


# ═══ 18 · the password leaks into output or command arguments ═══════════════

def test_mutation_18_the_password_never_reaches_output_or_argv():
    """LIVE, and asserted three ways because there are three ways to leak it.

    * **argv** — the tool must expose no URL/password option at all, since
      ``ps`` shows every argument to every user on the box.
    * **stdout/stderr** — the summary reports posture, never the secret.
    * **the wire** — a SCRAM verifier is sent instead of the plaintext, so the
      password is absent from the SQL text, the server log and
      ``pg_stat_activity``.

    The live half sets a password and RESTORES the exact one the environment
    supplied. Where the environment supplies none — a `trust`-authenticated
    developer cluster — the live half is skipped rather than guessed at: setting
    a password we could not put back would break the credential for every test
    after this one, and a mutation proof that sabotages the suite is not a proof.
    """
    from scripts.bootstrap_runtime_login import scram_verifier

    _skip_without_db()

    # ── argv: no option exists that could carry a secret ──────────────────
    helped = _run_tool([*_BOOTSTRAP, "--help"], {})
    for option in ("--migration-url", "--password", "--pw", "--url"):
        assert option not in helped.stdout, f"{option} would put a secret in argv"

    # ── the wire: the verifier is not the password ────────────────────────
    secret = "mutation-18-should-never-be-printed"
    verifier = scram_verifier(secret, salt=b"0123456789abcdef")
    assert secret not in verifier
    assert verifier.startswith("SCRAM-SHA-256$")

    # ── stdout/stderr: only with a password we can put back ───────────────
    original = os.environ.get("RUNTIME_DB_PASSWORD")
    if not original:
        pytest.skip(
            "RUNTIME_DB_PASSWORD is not set (trust-authenticated cluster) — the "
            "live password round-trip runs in CI, where it can be restored"
        )
    try:
        ran = _run_tool(_BOOTSTRAP, _bootstrap_env(RUNTIME_DB_PASSWORD=secret))
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert secret not in ran.stdout, "the password was printed to stdout"
        assert secret not in ran.stderr, "the password was printed to stderr"
        assert "password_set" in ran.stdout, "the posture summary went missing"
    finally:
        restored = _run_tool(_BOOTSTRAP, _bootstrap_env(RUNTIME_DB_PASSWORD=original))
        assert restored.returncode == 0, restored.stdout + restored.stderr


# ═══ 19 · the same-login FINAL posture is accepted ══════════════════════════

def test_mutation_19_same_login_final_posture():
    """LIVE (config + preflight). Already covered by #1/#1b for the boot guard;
    re-asserted here as the CUTOVER's end-state invariant, which is the thing an
    operator could reach by repointing one secret and forgetting the other."""
    with pytest.raises(ValueError, match="SAME role"):
        _prod_settings(
            database_url="postgresql+asyncpg://owner@db/muddato",
            database_url_sync="postgresql+psycopg://owner@db/muddato",
        )
    report = _runtime_report()
    report.migration_checked = True
    report.migration_owns_tables = 48
    report.migration_user = report.current_user
    report.migration_is_separate = False
    report.alembic_head = report.expected_head
    assert any("SAME" in f for f in _judged(report))


# ═══ 20 · Alembic falls back to the runtime credential ══════════════════════

def test_mutation_20_alembic_never_silently_uses_the_runtime_credential():
    """LIVE. `alembic/env.py` reads `settings.migration_url_sync`, which raises
    in production rather than returning `DATABASE_URL_SYNC`.

    The failure mode this prevents is not "a confusing error" — it is a release
    that appears to migrate, fails halfway through the DDL on a privilege it
    does not have, and leaves the schema between two revisions.
    """
    prod = _prod_settings()
    object.__setattr__(prod, "migration_database_url_sync", None)
    object.__setattr__(prod, "migration_database_url", None)
    with pytest.raises(RuntimeError, match="no fallback"):
        _ = prod.migration_url_sync

    # The env module must read that property, not the runtime URL. Asserted on
    # the source because importing it executes a migration context.
    from pathlib import Path

    env_py = (Path(__file__).resolve().parents[1] / "alembic" / "env.py").read_text()
    assert "settings.migration_url_sync" in env_py
    assert 'set_main_option("sqlalchemy.url", settings.database_url_sync)' not in env_py


# ═══ 21 · runtime falls back to the migration owner ═════════════════════════

def test_mutation_21_runtime_never_falls_back_to_the_owner():
    """There is no such code path, and the field is required so there cannot be."""
    from pydantic import ValidationError

    from app.config import Settings

    with patch.dict(os.environ, _CLEAN_ENV, clear=True), pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            _env_file=None, environment="production", secret_key="x" * 48,
            redis_url="redis://x", telegram_bot_token="1:a",
            migration_database_url_sync="postgresql+psycopg://owner@db/m",
        )
    report = _runtime_report()
    report.current_user = "postgres"
    assert any("expected" in f for f in _judged(report))


# ═══ 22 · the runtime login is widened ══════════════════════════════════════

@pytest.mark.parametrize(
    "widen,restore,expected",
    [
        (f"ALTER ROLE {ROLE_API_LOGIN} INHERIT",
         f"ALTER ROLE {ROLE_API_LOGIN} NOINHERIT", "NOINHERIT"),
        (f"GRANT CREATE ON SCHEMA public TO {ROLE_API_LOGIN}",
         f"REVOKE CREATE ON SCHEMA public FROM {ROLE_API_LOGIN}",
         "CREATE in schema public"),
        (f"GRANT SELECT ON users TO {ROLE_API_LOGIN}",
         f"REVOKE ALL ON users FROM {ROLE_API_LOGIN}", "RAW connection"),
    ],
)
def test_mutation_22_the_runtime_login_is_widened(widen, restore, expected):
    """LIVE. Three ways to widen the login, each caught by the preflight.

    The third is the subtle one: a single ``GRANT SELECT ON users`` turns the
    raw-connection probe from REFUSED into "permitted, returned rows" — which is
    exactly the posture the whole correction removes, reachable by one careless
    grant. INHERIT, BYPASSRLS and ownership are covered by #2, #3 and #4.
    """
    assert _judged_whole(_runtime_report()) == []
    try:
        _owner_exec((widen,))
        failures = _judged_whole(_runtime_report())
        assert any(expected in f for f in failures), (
            f"widening the login with `{widen}` was not flagged; got {failures}"
        )
    finally:
        _owner_exec((restore,))
    assert _judged_whole(_runtime_report()) == []


# ═══ 23 · previous-release transition compatibility is broken ═══════════════

def test_mutation_23_previous_release_transition_compatibility():
    """LIVE. The old executable must still serve on the new credential.

    Between repointing the runtime secrets and the new image going live, the OLD
    release may run against `muddato_api`. Its request path is a SQLAlchemy
    Session whose `after_begin` listener issues `SET LOCAL ROLE` — so it works,
    provided the login is a MEMBER of `muddato_app`. Revoke that membership and
    the window stops being survivable, which is what this asserts.
    """
    _, runtime_url = _skip_without_db()
    engine = create_engine(runtime_url)

    def _old_release_request() -> bool:
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL ROLE muddato_app"))
                conn.execute(text("SELECT set_config('app.tenant_id', '', true)"))
                conn.execute(text("SELECT count(*) FROM customers"))
            return True
        except Exception:
            return False

    try:
        assert _old_release_request(), "the baseline transition is already broken"
        _owner_exec((f"REVOKE muddato_app FROM {ROLE_API_LOGIN}",))
        assert not _old_release_request(), (
            "the old release still worked without the muddato_app membership — "
            "this test is not measuring the transition window"
        )
        assert any(
            "cannot assume" in f for f in _judged_whole(_runtime_report())
        ), "the preflight did not notice a missing authority membership"
    finally:
        _owner_exec((f"GRANT muddato_app TO {ROLE_API_LOGIN}",))
        engine.dispose()
    assert _judged_whole(_runtime_report()) == []


# ═══ 24 · the expired pypdf floor is restored ═══════════════════════════════

def test_mutation_24_expired_pypdf_floor_restored():
    """LIVE (in-module). Put the expired deferral back; the alarm must fire.

    The alarm's whole job is to make a lapsed promise loud. It went quiet only
    because the floor was actually raised, and this proves that is why —
    not because the alarm stopped working.
    """
    from datetime import date

    from tests import test_dated_dependency_floors as floors

    assert floors.DEFERRED_FLOORS == {}, "the deferral table should be empty"
    floors.test_no_deferral_is_expired()          # green today

    original = dict(floors.DEFERRED_FLOORS)
    try:
        floors.DEFERRED_FLOORS["pypdf"] = (
            ">=6.14.2,<7", date(2026, 8, 13), "the deferral that expired",
        )
        with pytest.raises(AssertionError, match=r"expired|deferred until"):
            floors.test_no_deferral_is_expired()
    finally:
        floors.DEFERRED_FLOORS.clear()
        floors.DEFERRED_FLOORS.update(original)
    floors.test_no_deferral_is_expired()


# ═══ 25 · the alarm is deleted without raising the floor ════════════════════

def test_mutation_25_deleting_the_alarm_without_raising_the_floor():
    """LIVE. Emptying the table is only honest if the floor really moved.

    The cheap way to silence a dated alarm is to delete its row. That works —
    and it is exactly why the floor is asserted somewhere the row cannot reach:
    `test_the_runtime_imports_the_locked_pypdf` pins the declared specifier, the
    locked version and the IMPORTED version to each other, so a deleted row with
    an unraised floor still fails.
    """
    from tests import test_dated_dependency_floors as floors

    floors.test_the_runtime_imports_the_locked_pypdf()   # green

    original = floors._declared_specifier
    try:
        floors._declared_specifier = lambda _pkg: ">=6.14.2,<7"   # the un-raised floor
        with pytest.raises(AssertionError, match=r"6\.15\.0"):
            floors.test_the_runtime_imports_the_locked_pypdf()
    finally:
        floors._declared_specifier = original
    floors.test_the_runtime_imports_the_locked_pypdf()


# ═══════════════════════════════════════════════════════════════════════════
# THIRD REVIEW PASS · mutations 26–34
#
# 17–25 proved the cutover. These prove the three gaps a third review found:
# a raw-login probe that only looked at one table, a preflight that never
# checked both credentials point at the SAME database, and a bootstrap path
# that put a password in argv.
# ═══════════════════════════════════════════════════════════════════════════


# ═══ 26 · a direct SELECT grant on a NON-users protected table ══════════════

def test_mutation_26_direct_select_grant_on_payments():
    """LIVE. The exact escape the third review found.

    The raw probe used to ask `users` and nothing else, so
    `GRANT SELECT ON payments TO muddato_api` slipped through: `users` stayed
    denied, the headline said "raw connection refused", and the later per-table
    probes assume an authority role first — measuring the authority, not the
    login.
    """
    assert _judged_whole(_runtime_report()) == []
    try:
        _owner_exec((f"GRANT SELECT ON payments TO {ROLE_API_LOGIN}",))
        failures = _judged_whole(_runtime_report())
        assert any("payments" in f for f in failures), (
            f"a direct SELECT grant on payments was not flagged; got {failures}"
        )
        # Both detectors must see it, not just one — the catalogue and the probe
        # are meant to be independent, and a mutation that only trips one of
        # them means the other has quietly stopped working.
        report = _runtime_report()
        assert "payments:SELECT" in report.direct_table_grants
        assert "payments" in report.raw_permitted_tables
    finally:
        _owner_exec((f"REVOKE ALL ON payments FROM {ROLE_API_LOGIN}",))
    assert _judged_whole(_runtime_report()) == []


# ═══ 27 · direct write grants on a non-users protected table ════════════════

@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE", "DELETE"])
def test_mutation_27_direct_write_grant(privilege):
    """LIVE. Writes cannot be probed read-only, so the CATALOGUE carries these.

    A preflight that only ran SELECT probes would report a clean posture while
    the login held `INSERT ON customers` — visible in `aclexplode`, invisible to
    any read-only experiment.
    """
    assert _judged_whole(_runtime_report()) == []
    try:
        _owner_exec((f"GRANT {privilege} ON customers TO {ROLE_API_LOGIN}",))
        report = _runtime_report()
        assert f"customers:{privilege}" in report.direct_table_grants
        assert any(
            "direct table privilege" in f for f in _judged_whole(_runtime_report())
        ), f"a direct {privilege} grant was not flagged"
    finally:
        _owner_exec((f"REVOKE ALL ON customers FROM {ROLE_API_LOGIN}",))
    assert _judged_whole(_runtime_report()) == []


# ═══ 28 · a sequence grant ══════════════════════════════════════════════════

def test_mutation_28_sequence_grant():
    """LIVE. The schema has no sequences today (every key is a UUID), so this
    creates one, grants it, and proves the sweep is not vacuous by construction.

    Without this the "0/0 sequences refused" line would be untested code that
    happens to print a zero — which is exactly how the single-table raw probe
    survived two reviews.
    """
    assert _judged_whole(_runtime_report()) == []
    _owner_exec(("CREATE SEQUENCE IF NOT EXISTS mutation_probe_seq",))
    try:
        clean = _runtime_report()
        assert clean.raw_probed_sequences >= 1, "the sequence sweep did not run"
        assert clean.raw_permitted_sequences == []
        assert _judged_whole(clean) == []

        _owner_exec((f"GRANT SELECT, USAGE ON SEQUENCE mutation_probe_seq "
                     f"TO {ROLE_API_LOGIN}",))
        report = _runtime_report()
        assert "mutation_probe_seq" in report.raw_permitted_sequences
        assert any("mutation_probe_seq" in g for g in report.direct_sequence_grants)
        assert any(
            "sequence" in f for f in _judged_whole(_runtime_report())
        ), "a direct sequence grant was not flagged"
    finally:
        _owner_exec((
            f"REVOKE ALL ON SEQUENCE mutation_probe_seq FROM {ROLE_API_LOGIN}",
            "DROP SEQUENCE IF EXISTS mutation_probe_seq",
        ))
    assert _judged_whole(_runtime_report()) == []


# ═══ 29 · a grant through an unexpected intermediary role ═══════════════════

def test_mutation_29_grant_through_an_intermediary_role():
    """LIVE. The subtle one: nothing names the login.

    `GRANT SELECT ON payments TO sneaky; GRANT sneaky TO muddato_app` gives the
    login a route to `payments` without adding a single catalogue row that
    mentions `muddato_api`. The direct-grant query sees nothing and the direct
    membership list sees nothing — only the TRANSITIVE reachability check does.
    """
    assert _judged_whole(_runtime_report()) == []
    try:
        _owner_exec((
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE "
            "rolname = 'mutation_probe_sneaky') THEN "
            "CREATE ROLE mutation_probe_sneaky NOLOGIN; END IF; END $$",
            "GRANT SELECT ON payments TO mutation_probe_sneaky",
            "GRANT mutation_probe_sneaky TO muddato_app",
        ))
        report = _runtime_report()
        assert "mutation_probe_sneaky" in report.reachable_roles
        assert "mutation_probe_sneaky" not in report.login_memberships, (
            "the intermediary showed up as a DIRECT membership — this test is "
            "not exercising the transitive path it exists for"
        )
        assert any(
            "transitively" in f for f in _judged_whole(report)
        ), "an unexpected intermediary role was not flagged"
    finally:
        _owner_exec((
            "REVOKE mutation_probe_sneaky FROM muddato_app",
            "REVOKE ALL ON payments FROM mutation_probe_sneaky",
            "DROP ROLE IF EXISTS mutation_probe_sneaky",
        ))
    assert _judged_whole(_runtime_report()) == []


# ═══ 30 · the login owns a NON-users protected table ════════════════════════

def test_mutation_30_login_owns_a_non_users_table():
    """LIVE where the cluster allows it, JUDGEMENT where it does not.

    `ALTER TABLE … OWNER TO` requires the current owner to be a member of the
    target role, which the schema owner deliberately is not. Where a superuser
    URL is available the real re-owning runs; otherwise the fact is injected and
    the same assertion is exercised.
    """
    su = os.environ.get("TEST_SUPERUSER_DATABASE_URL")
    if su:
        engine = create_engine(_dbcreds.sync(su), isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE payments OWNER TO {ROLE_API_LOGIN}"))
            report = _runtime_report()
            assert "payments" in report.owned_tables
            assert any("OWNS" in f for f in _judged_whole(report))
        finally:
            owner = make_url(_dbcreds.sync(_dbcreds.migration_url())).username
            with engine.connect() as conn:
                conn.execute(text(f"ALTER TABLE payments OWNER TO {owner}"))
            engine.dispose()
        assert _judged_whole(_runtime_report()) == []
        return

    report = _runtime_report()
    report.owned_tables = ["payments"]
    assert any("OWNS" in f and "payments" in f for f in _judged(report))


# ═══ 31 · default privileges that would re-open future objects ══════════════

def test_mutation_31_default_privileges_grant_future_objects():
    """LIVE. A clean database today that re-opens itself at the next migration.

    `ALTER DEFAULT PRIVILEGES … GRANT SELECT ON TABLES TO muddato_api` changes
    nothing that exists, so every probe over today's objects stays green — and
    the next table any migration creates arrives readable by the bare login.
    """
    assert _judged_whole(_runtime_report()) == []
    try:
        _owner_exec((
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT ON TABLES TO {ROLE_API_LOGIN}",
        ))
        report = _runtime_report()
        assert report.default_privilege_grants, "the default ACL was not seen"
        assert any(
            "ALTER DEFAULT PRIVILEGES" in f for f in _judged_whole(report)
        ), "a future-granting default privilege was not flagged"
    finally:
        _owner_exec((
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE ALL ON TABLES FROM {ROLE_API_LOGIN}",
        ))
    assert _judged_whole(_runtime_report()) == []


# ═══ 32 · the migration URL points at a head-equivalent WRONG database ══════

def test_mutation_32_migration_url_points_at_another_database():
    """LIVE, and the negative control the third review asked for.

    A clone with the same Alembic head, the same roles and the same policies is
    indistinguishable from the real database by everything the preflight checked
    before this: same head, both logins present, both postures clean. The deploy
    would migrate the clone and the application would keep using the original.

    So a second database is really built, migrated to the same head, and pointed
    at — and the preflight must go RED on identity alone.
    """
    from sqlalchemy.engine import make_url as _make_url

    owner_url, _runtime_url = _skip_without_db()
    decoy = "muddato_wrong_database_control"
    # `str(URL)` renders the password as the literal `***` — SQLAlchemy hides it
    # on purpose, and a URL built that way authenticates as `***`. Harmless on a
    # `trust` developer cluster, which is why this only ever surfaced in CI.
    admin = _make_url(owner_url).set(database="postgres").render_as_string(
        hide_password=False)
    owner_name = _make_url(owner_url).username

    def _admin(statement: str) -> None:
        engine = create_engine(admin, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(text(statement))
        finally:
            engine.dispose()

    import subprocess
    import sys
    from pathlib import Path

    _admin(f'DROP DATABASE IF EXISTS "{decoy}"')
    _admin(f'CREATE DATABASE "{decoy}" OWNER "{owner_name}"')
    decoy_url = _make_url(owner_url).set(database=decoy).render_as_string(
        hide_password=False)
    try:
        migrated = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env={**os.environ, "MIGRATION_DATABASE_URL_SYNC": decoy_url},
        )
        assert migrated.returncode == 0, migrated.stderr[-1500:]

        # The decoy really is head-equivalent — otherwise this proves nothing.
        engine = create_engine(decoy_url)
        try:
            with engine.connect() as conn:
                head = conn.execute(
                    text("SELECT version_num FROM alembic_version")).scalar()
        finally:
            engine.dispose()
        real = _runtime_report()
        assert head == real.expected_head, (
            "the decoy is not at the same head, so it is not a control"
        )

        # Now the actual mutation: runtime on the real database, migration on
        # the decoy.
        report = _runtime_report()
        mig_engine = create_engine(decoy_url)
        try:
            with mig_engine.connect() as conn:
                pf.collect_migration(
                    conn, report, url=decoy_url, runtime_user=report.current_user,
                )
        finally:
            mig_engine.dispose()
        pf.check_webhook_routes(report)
        failures = _judged(report)
        assert any("database identity" in f for f in failures), (
            f"pointing the migration URL at a head-equivalent clone passed; "
            f"got {failures}"
        )
        assert report.same_database is False
    finally:
        _admin(f'DROP DATABASE IF EXISTS "{decoy}"')

    # …and the correct URL is GREEN again.
    assert _judged_whole(_runtime_report()) == []


# ═══ 33 · the argv password path is gone ════════════════════════════════════

def test_mutation_33_no_argv_password_path_exists():
    """The `psql -v pw=…` companion is deleted, not merely discouraged.

    A `-v` assignment is a process argument, so the file contradicted the
    project's own invariant every time an operator followed it. Its absence is
    asserted, because a deleted file is the only kind that cannot be run by
    mistake.
    """
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    assert not (scripts / "bootstrap_runtime_login.sql").exists(), (
        "the SQL bootstrap is back; it puts the password in argv via `psql -v`"
    )
    assert not (scripts / "provision_rls_role.sql").exists()

    # And no surviving file tells anybody to do it.
    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in list(root.glob("docs/**/*.md")) + list(scripts.glob("*")) + [
        root / ".env.example", root / "backend" / "fly.toml",
    ]:
        if not path.is_file():
            continue
        body = path.read_text(errors="ignore")
        if "-v pw=" in body or "PASSWORD :pw" in body:
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"argv-password examples survive in: {offenders}"


# ═══ 34 · the bootstrap leaks the password into argv, output or errors ══════

def test_mutation_34_bootstrap_leaks_nothing():
    """LIVE, with the process inspected while it runs.

    Four surfaces, because a secret escapes through whichever one nobody
    watched: `argv` (world-readable via `ps`), stdout, stderr, and the SQL the
    driver quotes back inside an exception.
    """
    import secrets
    import subprocess
    import sys
    import threading
    from pathlib import Path

    from scripts.bootstrap_runtime_login import scram_verifier

    _skip_without_db()
    # Synthetic, generated here, used nowhere else, and restored afterwards.
    probe_secret = "probe-" + secrets.token_urlsafe(24)
    original = os.environ.get("RUNTIME_DB_PASSWORD")

    env = {
        **os.environ,
        "MUDDATO_BOOTSTRAP_MIGRATION_URL": _dbcreds.sync(_dbcreds.migration_url()),
        "RUNTIME_DB_PASSWORD": probe_secret,
    }
    seen_argv: list[str] = []

    def _watch(pid: int, stop: threading.Event) -> None:
        """Sample the child's own command line, as any user on the box could."""
        while not stop.is_set():
            got = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True, text=True,
            )
            line = got.stdout.strip()
            if line:
                seen_argv.append(line)
            stop.wait(0.005)

    proc = subprocess.Popen(
        [sys.executable, "-m", "scripts.bootstrap_runtime_login"],
        cwd=Path(__file__).resolve().parents[1], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    stop = threading.Event()
    watcher = threading.Thread(target=_watch, args=(proc.pid, stop), daemon=True)
    watcher.start()
    out, err = proc.communicate()
    stop.set()
    watcher.join(timeout=2)

    try:
        assert proc.returncode == 0, out + err

        verifier = scram_verifier(probe_secret, salt=b"0123456789abcdef")
        # argv — observed live where the process lived long enough, and
        # structurally regardless: the tool has no option that could carry it.
        for line in seen_argv:
            assert probe_secret not in line, "the password appeared in ps output"
            assert "SCRAM-SHA-256" not in line, "a verifier appeared in ps output"
        assert seen_argv, (
            "the process was never observed — ps sampling caught nothing, so "
            "this assertion proved nothing"
        )
        # stdout / stderr
        assert probe_secret not in out and probe_secret not in err
        assert "SCRAM-SHA-256" not in out and "SCRAM-SHA-256" not in err
        assert verifier not in out and verifier not in err
        # the summary still says something useful
        assert "password_set" in out

        # exception messages: force a failure with a password set and confirm
        # neither the plaintext nor a verifier reaches the message.
        broken = subprocess.run(
            [sys.executable, "-m", "scripts.bootstrap_runtime_login"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
            env={**env, "MUDDATO_BOOTSTRAP_MIGRATION_URL":
                 _dbcreds.sync(_dbcreds.runtime_url())},
        )
        assert broken.returncode != 0
        assert probe_secret not in broken.stdout + broken.stderr
        assert "SCRAM-SHA-256" not in broken.stdout + broken.stderr
    finally:
        if original:
            subprocess.run(
                [sys.executable, "-m", "scripts.bootstrap_runtime_login"],
                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                env={**env, "RUNTIME_DB_PASSWORD": original},
            )
