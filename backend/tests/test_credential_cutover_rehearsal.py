"""The credential cutover, rehearsed from the REAL pre-rg0 production posture.

The previous correction shipped a cutover sequence that could not be executed.
It began "set `MIGRATION_DATABASE_URL_SYNC` and deploy", from a live posture
where `DATABASE_URL` is still the schema owner — which makes both URLs name the
same login, which makes `app/config.py` raise at MODULE IMPORT, which makes
`alembic/env.py` unimportable, which means `rg0credsplit` never runs and
`muddato_api` is never created. A prose fix would not have found that; only
starting from the actual posture does.

So this module starts there, on its own throwaway database:

    schema at rf0failclosed · runtime URLs = the owner · muddato_api absent
    · no password anywhere · representative tenant data present

and drives the whole sequence, asserting at each step. It is a test rather than
a script so it runs in CI and cannot quietly rot, and it prints an operator
transcript so the RUNBOOK's steps and this file's steps are visibly the same
steps.

Nothing here touches production. The database is created and dropped by the
module, named for what it is.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.core.rls_manifest import AUTHORITY_ROLES, ROLE_API_LOGIN
from tests import _dbcreds


def _chain_tip() -> str:
    """(MUDD-66) Where ``alembic upgrade head`` lands today.

    Was the literal ``"rg0credsplit"``, which made every later migration —
    including ones with nothing to do with credentials — fail this rehearsal.
    What the rehearsal is measuring is that the OWNER migrated and the runtime
    credential could not, not which revision happens to be newest.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert len(heads) == 1, f"expected a single head, found {heads}"
    return heads[0]


#: A throwaway database, named for what it is so nobody mistakes it for a real
#: one. Created and dropped by this module.
REHEARSAL_DB = "muddato_cutover_rehearsal"

#: Not a secret: it exists for the lifetime of one test run against a database
#: this module creates and destroys. The real one is a deploy secret and appears
#: nowhere in this repository.
REHEARSAL_PASSWORD = "rehearsal-only-not-a-real-secret"

BACKEND = Path(__file__).resolve().parents[1]


# ── plumbing ─────────────────────────────────────────────────────────────────

def _owner_admin_url() -> str:
    """The owner's URL against the maintenance database, for CREATE/DROP DATABASE."""
    base = _dbcreds.migration_url()
    if not base:
        pytest.skip("TEST_DATABASE_URL not set — the cutover rehearsal needs Postgres")
    # `render_as_string(hide_password=False)`, never `str()`: the latter renders
    # the password as the literal `***`, and the URL then authenticates as `***`.
    return _dbcreds.sync(
        make_url(base).set(database="postgres").render_as_string(hide_password=False))


#: "no password argument" and "explicitly no password" are different requests,
#: and only a sentinel can tell them apart.
_INHERIT: Any = object()


def _url(user: str, *, password: Any = _INHERIT) -> str:
    """``user``'s URL against the throwaway database.

    Without an explicit ``password`` the environment's own is kept. Defaulting
    to None instead built a passwordless URL, which authenticates fine on a
    `trust` developer cluster and is refused by every scram one — so the owner
    half of this module could only ever pass locally.
    """
    base = make_url(_dbcreds.sync(_dbcreds.migration_url()))
    pw = base.password if password is _INHERIT else password
    return base.set(
        username=user, password=pw, database=REHEARSAL_DB,
    ).render_as_string(hide_password=False)


def _run(
    argv: list[str], env: dict[str, str], *, merge: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run an operator command exactly as an operator would — a subprocess.

    In-process calls would import `app.config` in THIS interpreter, where it is
    already loaded with the test configuration. The deadlock under test is an
    import-time one, so it only reproduces in a fresh interpreter.

    ``merge=True`` (the default every other call site here relies on) overlays
    ``env`` onto this process's own environment, so PATH/HOME/the active venv
    keep working with only the named variables overridden. A caller asserting
    behaviour when a variable is ABSENT needs ``merge=False``: this test
    process's own environment can already carry that variable (the CI job's
    ``env:`` block sets ``MUDDATO_BOOTSTRAP_MIGRATION_URL`` for the whole
    `integration` job, including the step that runs this suite), and a merge
    can only ADD or override keys — it can never unset one, so the child would
    silently inherit the very value the test means to omit.
    """
    return subprocess.run(
        [sys.executable, *argv], cwd=BACKEND, capture_output=True, text=True,
        env={**os.environ, **env} if merge else dict(env),
    )


def _alembic(url: str, revision: str = "head", *, extra: dict[str, str] | None = None):
    return _run(
        ["-m", "alembic", "upgrade", revision],
        {"MIGRATION_DATABASE_URL_SYNC": url, **(extra or {})},
    )


def _sql(url: str, statement: str, params: dict | None = None):
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            result = conn.execute(text(statement), params or {})
            return result.all() if result.returns_rows else []
    finally:
        engine.dispose()


def _drop_rehearsal_db(admin: str) -> None:
    """Remove the throwaway database, tolerating backends we may not terminate.

    A forced drop terminates other sessions, and terminating a session that
    belongs to ANOTHER role needs ``pg_signal_backend`` — which the schema owner
    deliberately does not have. Half this module connects as ``muddato_api``, so
    the forced form can fail on exactly the connections the rehearsal is about.

    So: ask politely, retry while sockets from finished subprocesses close, and
    warn rather than raise if something still holds it. A leftover throwaway
    database costs nothing — the next run reclaims it at setup — whereas a
    teardown that fails the suite hides whatever the tests actually found.
    """
    import time

    for attempt in range(10):
        try:
            _sql(admin, f'DROP DATABASE IF EXISTS "{REHEARSAL_DB}"')
            return
        except Exception:
            if attempt == 0:
                # Best effort, and only the backends we are allowed to signal.
                with contextlib.suppress(Exception):
                    _sql(
                        admin,
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :d AND pid <> pg_backend_pid()",
                        {"d": REHEARSAL_DB},
                    )
            time.sleep(0.2)
    print(f"WARNING: could not remove {REHEARSAL_DB}; the next run will reclaim it")


def _tenant_fingerprint(url: str) -> tuple:
    """Row counts plus a content checksum, so "no data changed" is measurable.

    Counts alone would miss a rewrite that preserved cardinality; the checksum
    would miss a pure deletion. Together they are hard to fool by accident.
    """
    return tuple(_sql(url, """
        SELECT (SELECT count(*) FROM tenants),
               (SELECT count(*) FROM users),
               (SELECT count(*) FROM customers),
               (SELECT md5(string_agg(t.slug, ',' ORDER BY t.slug)) FROM tenants t),
               (SELECT md5(string_agg(c.phone, ',' ORDER BY c.phone)) FROM customers c)
    """)[0])


# ── this module is a SEQUENCE, and says so ───────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def _requires_the_whole_module(request):
    """Refuse to run a partial selection of this module.

    These tests walk one cutover in order: bootstrap creates the login, a later
    test sets its password, a later one migrates to rg0. Selecting a subset —
    `-k document` matches "documented" in one of the names — drops the steps
    that establish the state and produces a failure that looks like a defect in
    the thing under test rather than in the selection.

    Skipping is the honest answer for a partial run: the full suite, which is
    the gate, always selects everything and never reaches this branch.
    """
    selected = sum(
        1 for item in request.session.items
        if item.module.__name__ == __name__
    )
    defined = sum(
        1 for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    )
    if selected < defined:
        pytest.skip(
            f"the cutover rehearsal is an ordered sequence: {selected} of "
            f"{defined} tests were selected, and the earlier steps establish the "
            "state the later ones need. Run the whole module."
        )


# ── the starting posture ─────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pre_cutover():
    """A database in the EXACT posture production is in today.

    Schema at `rf0failclosed`, seeded, and — the part that matters — with no
    usable `muddato_api`. The role is dropped if a previous run left one behind,
    because a rehearsal that starts from an already-bootstrapped cluster proves
    nothing about bootstrapping.
    """
    admin = _owner_admin_url()
    _drop_rehearsal_db(admin)
    owner_name = make_url(_dbcreds.sync(_dbcreds.migration_url())).username
    _sql(admin, f'CREATE DATABASE "{REHEARSAL_DB}" OWNER "{owner_name}"')

    owner_url = _url(owner_name)
    # Start from a cluster where the login genuinely does not work: no password,
    # and (if a stale role survives from an earlier run) no memberships either.
    _sql(admin, f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname="
                f"'{ROLE_API_LOGIN}') THEN ALTER ROLE {ROLE_API_LOGIN} "
                f"NOLOGIN PASSWORD NULL; END IF; END $$")

    result = _alembic(owner_url, "rf0failclosed")
    assert result.returncode == 0, result.stderr[-2000:]

    from scripts.rehearse_rg0_migration import SCALE, _seed

    engine = create_engine(owner_url)
    try:
        _seed(engine, SCALE)
    finally:
        engine.dispose()

    try:
        yield owner_url
    finally:
        _sql(admin, f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname="
                    f"'{ROLE_API_LOGIN}') THEN ALTER ROLE {ROLE_API_LOGIN} "
                    f"NOLOGIN PASSWORD NULL; END IF; END $$")
        _drop_rehearsal_db(admin)
        _restore_the_shared_login()


def _restore_the_shared_login() -> None:
    """Put ``muddato_api`` back the way the suite found it.

    A ROLE is cluster-global, not per-database: the throwaway database goes
    away, the login it was rehearsed on does NOT. This module deliberately
    breaks that login (``NOLOGIN PASSWORD NULL``) to rehearse a bootstrap from
    the real pre-rg0 posture, so without this every later test that connects as
    the restricted credential fails on authentication — 94 of them, in a full
    run against a scram cluster. It never showed up before because the module
    only ever ran where the login needed no password.

    Restored with the production tool against the real migration URL, i.e. the
    exact command CI's provisioning step runs, so the end state is the same one
    the session started from rather than an approximation of it.
    """
    migration = _dbcreds.migration_url()
    if not migration:                                   # nothing was set up
        return
    _run(["-m", "scripts.bootstrap_runtime_login"],
         {"MUDDATO_BOOTSTRAP_MIGRATION_URL": _dbcreds.sync(migration)})


# ── 0 · the deadlock this correction exists to break ─────────────────────────

def test_the_old_documented_sequence_is_impossible(pre_cutover):
    """Step 1 of the previous cutover doc, run for real. It must fail.

    "Set MIGRATION_DATABASE_URL_SYNC and deploy" — from a posture where
    DATABASE_URL is still the owner, both URLs name the same login, and
    `app/config.py` raises while `alembic/env.py` is importing it. The migration
    that would create `muddato_api` therefore never runs.

    Asserted rather than described, because a contradiction that is only written
    down gets rewritten instead of fixed.
    """
    owner = pre_cutover
    result = _alembic(owner, "head", extra={
        "ENVIRONMENT": "production",
        "SECRET_KEY": "x" * 48,
        "RISK_HASH_SALT": "not-the-dev-default",
        "ALLOWED_ORIGINS": "https://muddato.uz",
        "DATABASE_URL": owner.replace("+psycopg", "+asyncpg"),
        "DATABASE_URL_SYNC": owner,
    })
    assert result.returncode != 0, (
        "the pre-cutover posture no longer deadlocks — if that is deliberate, "
        "the identity-split guard has been weakened"
    )
    assert "SAME role" in result.stderr, result.stderr[-1500:]

    # And the schema is untouched: the deadlock is a refusal, not a half-migration.
    assert _sql(owner, "SELECT version_num FROM alembic_version")[0][0] == "rf0failclosed"


# ── 1 · bootstrap creates the restricted login ───────────────────────────────

def test_bootstrap_creates_the_login_from_the_pre_cutover_posture(pre_cutover):
    owner = pre_cutover
    before = _tenant_fingerprint(owner)

    result = _run(
        ["-m", "scripts.bootstrap_runtime_login"],
        {
            "MUDDATO_BOOTSTRAP_MIGRATION_URL": owner,
            "RUNTIME_DB_PASSWORD": REHEARSAL_PASSWORD,
            # Deliberately hostile: the deployed configuration is still the
            # single-credential one, so if the tool touched `app.config` at all
            # it would die exactly as Alembic did above.
            "ENVIRONMENT": "production",
            "DATABASE_URL": owner.replace("+psycopg", "+asyncpg"),
            "DATABASE_URL_SYNC": owner,
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr

    row = _sql(owner, "SELECT rolcanlogin, rolinherit, rolsuper, rolbypassrls "
                      "FROM pg_roles WHERE rolname = :r", {"r": ROLE_API_LOGIN})[0]
    assert row == (True, False, False, False), row
    # Whether the password took is settled by AUTHENTICATING, not by reading a
    # catalogue the owner is not allowed to read (pg_authid is superuser-only).
    # `--verify` is that proof, in the next test.

    # It grants exactly the authority roles that EXIST, and no others.
    #
    # Computed rather than hardcoded, because PostgreSQL roles are CLUSTER-scoped:
    # a developer machine that has ever migrated any database to rg0credsplit
    # already has `muddato_webhook`, even though a real pre-rg0 production cluster
    # does not. Hardcoding "webhook is absent" would pass on a clean CI runner and
    # fail on every second local run — testing the machine, not the tool. The
    # absent-role branch is proved directly in
    # `test_the_bootstrap_skips_authority_roles_that_do_not_exist_yet`.
    present = {r for (r,) in _sql(
        owner, "SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)",
        {"names": list(AUTHORITY_ROLES)})}
    granted = {r for (r,) in _sql(owner,
        "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid=m.roleid "
        "JOIN pg_roles u ON u.oid=m.member WHERE u.rolname = :r", {"r": ROLE_API_LOGIN})}
    assert granted == present
    if present != set(AUTHORITY_ROLES):
        assert "pending until rg0credsplit" in result.stdout

    assert _tenant_fingerprint(owner) == before, "bootstrap changed tenant data"


def test_bootstrap_is_idempotent_and_narrows_on_every_run(pre_cutover):
    """Re-running converges, and REPAIRS a role somebody widened by hand."""
    owner = pre_cutover
    _sql(owner, f"ALTER ROLE {ROLE_API_LOGIN} INHERIT")
    _sql(owner, f"GRANT CREATE ON SCHEMA public TO {ROLE_API_LOGIN}")

    # The password is pinned, not inherited. `_run` merges over `os.environ`,
    # so an ambient RUNTIME_DB_PASSWORD (CI's `integration` job sets one for the
    # whole job) would make this re-run silently REPLACE the credential the
    # rehearsal established two tests ago — and every later test here connects
    # with REHEARSAL_PASSWORD. It passed locally only because a developer shell
    # has no such variable to inherit.
    result = _run(["-m", "scripts.bootstrap_runtime_login"],
                  {"MUDDATO_BOOTSTRAP_MIGRATION_URL": owner,
                   "RUNTIME_DB_PASSWORD": REHEARSAL_PASSWORD})
    assert result.returncode == 0, result.stdout + result.stderr

    row = _sql(owner, "SELECT rolinherit FROM pg_roles WHERE rolname = :r",
               {"r": ROLE_API_LOGIN})[0]
    assert row[0] is False, "re-running did not restore NOINHERIT"
    assert _sql(owner, "SELECT has_schema_privilege(:r, 'public', 'CREATE')",
                {"r": ROLE_API_LOGIN})[0][0] is False


# ── 2 · verify, through the restricted login itself ──────────────────────────

def test_verify_proves_the_login_from_the_inside(pre_cutover):
    runtime = _url(ROLE_API_LOGIN, password=REHEARSAL_PASSWORD)
    result = _run(["-m", "scripts.bootstrap_runtime_login", "--verify"],
                  {"MUDDATO_BOOTSTRAP_VERIFY_URL": runtime})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "raw read of users         refused" in result.stdout
    assert "VERIFIED" in result.stdout


# ── 3 · the previous release keeps working on the new credential ─────────────

def test_the_previous_release_still_serves_on_the_restricted_login(pre_cutover):
    """The transition window, measured rather than assumed.

    Between repointing the runtime secrets and the new image being live, the OLD
    executable may run against the NEW credential. Its request path is a
    SQLAlchemy Session, whose `after_begin` listener issues `SET LOCAL ROLE`
    exactly as the new one does — so at `rf0failclosed`, with the memberships
    the bootstrap granted, tenant work still resolves. That is what makes the
    window survivable, and it is asserted here so a future change to the
    listener cannot quietly remove it.
    """
    runtime = _url(ROLE_API_LOGIN, password=REHEARSAL_PASSWORD)
    owner = pre_cutover
    tenant_id = _sql(owner, "SELECT id FROM tenants ORDER BY slug LIMIT 1")[0][0]

    engine = create_engine(runtime)
    try:
        with engine.begin() as conn:
            # Precisely what the old release's after_begin listener emits.
            conn.execute(text("SET LOCAL ROLE muddato_app"))
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                         {"t": str(tenant_id)})
            seen = conn.execute(text("SELECT count(*) FROM customers")).scalar()
        assert seen and seen > 0, (
            "the previous release cannot read its own tenant's rows on the new "
            "credential — the transition window is NOT survivable"
        )
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE muddato_identity"))
            conn.execute(text("SELECT set_config('app.user_id', '', true)"))
            # rf0's identity policy is still USING(true) here — that is the
            # posture rg0 closes, and the old release depends on it to log
            # anybody in. It must still work until rg0 lands.
            assert conn.execute(text("SELECT count(*) FROM users")).scalar() > 0
    finally:
        engine.dispose()


def test_the_previous_releases_jobstore_is_the_documented_exception(pre_cutover):
    """And the one thing that does NOT survive the window, stated plainly.

    The old image hands APScheduler `DATABASE_URL_SYNC` verbatim; its jobstore
    holds its own connections and never issues `SET LOCAL ROLE`, so on the
    restricted login it cannot reach `apscheduler_jobs`. The new image pins
    `role=muddato_worker` at connect and does not have this problem.

    This is asserted, not hidden, because the RUNBOOK's mitigation — stage the
    secrets so the old image never sees the new credential, and scale the worker
    to zero if the deploy is retried — is only justified if the failure is real.
    """
    runtime = _url(ROLE_API_LOGIN, password=REHEARSAL_PASSWORD)
    engine = create_engine(runtime)
    try:
        with pytest.raises(DBAPIError) as caught, engine.begin() as conn:
            conn.execute(text("SELECT 1 FROM apscheduler_jobs LIMIT 1"))
        assert "permission denied" in str(caught.value).lower()

        # The new image's shape, for contrast: pinned at connect, works.
        pinned = create_engine(
            runtime, connect_args={"options": "-c role=muddato_worker"},
        )
        try:
            with pinned.begin() as conn:
                conn.execute(text("SELECT 1 FROM apscheduler_jobs LIMIT 1"))
        finally:
            pinned.dispose()
    finally:
        engine.dispose()


# ── 4 · the migration runs, on the owner and only the owner ──────────────────

def test_rg0_migrates_with_the_owner_and_refuses_the_runtime_credential(pre_cutover):
    owner = pre_cutover
    runtime = _url(ROLE_API_LOGIN, password=REHEARSAL_PASSWORD)
    before = _tenant_fingerprint(owner)

    # The runtime credential must not be able to migrate, even if handed to
    # Alembic directly — the restriction is in the database, not the config.
    denied = _alembic(runtime, "head")
    assert denied.returncode != 0, "the restricted login performed migration DDL"
    assert _sql(owner, "SELECT version_num FROM alembic_version")[0][0] == "rf0failclosed"

    applied = _alembic(owner, "head")
    assert applied.returncode == 0, applied.stderr[-2000:]
    assert _sql(owner, "SELECT version_num FROM alembic_version")[0][0] == _chain_tip()
    assert _tenant_fingerprint(owner) == before, "the migration changed tenant data"

    # rg0 completes the membership the bootstrap could not grant yet.
    granted = {r for (r,) in _sql(owner,
        "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid=m.roleid "
        "JOIN pg_roles u ON u.oid=m.member WHERE u.rolname = :r", {"r": ROLE_API_LOGIN})}
    assert granted == set(AUTHORITY_ROLES)


# ── 5 · the release-time preflight, on both credentials ──────────────────────

def test_the_preflight_passes_with_both_credentials(pre_cutover):
    owner = pre_cutover
    runtime = _url(ROLE_API_LOGIN, password=REHEARSAL_PASSWORD)
    result = _run(
        ["-m", "app.cli.rls_preflight", "--url", runtime, "--migration-url", owner],
        {},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    # The bare-login sweep, not a single-table headline: every table refused.
    assert "bare-login tables" in result.stdout
    assert "permitted=0, leaked=0" in result.stdout
    assert "separate from runtime    True" in result.stdout


def test_a_failed_preflight_leaves_the_schema_and_old_release_usable(pre_cutover):
    """The abort path. A refused deploy must cost nothing."""
    owner = pre_cutover
    # Point BOTH at the owner — the posture the preflight must refuse.
    result = _run(
        ["-m", "app.cli.rls_preflight", "--url", owner, "--migration-url", owner], {},
    )
    assert result.returncode == 1
    assert _sql(owner, "SELECT version_num FROM alembic_version")[0][0] == _chain_tip()
    assert _tenant_fingerprint(owner)[0] > 0, "the failed preflight cost data"


# ── 6 · the final posture ────────────────────────────────────────────────────

def test_the_raw_runtime_connection_is_denied_after_cutover(pre_cutover):
    runtime = _url(ROLE_API_LOGIN, password=REHEARSAL_PASSWORD)
    engine = create_engine(runtime)
    try:
        for table in ("users", "customers", "tenants", "tenant_settings"):
            with pytest.raises(DBAPIError) as caught, engine.begin() as conn:
                conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
            assert "permission denied" in str(caught.value).lower()
    finally:
        engine.dispose()


def test_api_worker_and_scheduler_assume_only_their_intended_authorities(pre_cutover):
    """Each process shape, exercised as it actually connects."""
    runtime = _url(ROLE_API_LOGIN, password=REHEARSAL_PASSWORD)
    owner = pre_cutover
    tenant_id = _sql(owner, "SELECT id FROM tenants ORDER BY slug LIMIT 1")[0][0]

    engine = create_engine(runtime)
    try:
        # API: muddato_app, scoped — sees its tenant and nothing else.
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE muddato_app"))
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                         {"t": str(tenant_id)})
            others = conn.execute(text(
                "SELECT count(*) FROM customers WHERE tenant_id <> :t"),
                {"t": str(tenant_id)}).scalar()
        assert others == 0

        # Worker / cron: cross-tenant by design.
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE muddato_worker"))
            assert conn.execute(text("SELECT count(*) FROM customers")).scalar() > 0

        # Scheduler: the jobstore's connect-time pin.
        pinned = create_engine(runtime, connect_args={"options": "-c role=muddato_worker"})
        try:
            with pinned.begin() as conn:
                assert conn.execute(text("SELECT current_user")).scalar() == "muddato_worker"
                conn.execute(text("SELECT 1 FROM apscheduler_jobs LIMIT 1"))
        finally:
            pinned.dispose()

        # And the one role it can reach that is NOT an authority grants nothing.
        #
        # `SET ROLE` permits any role held transitively, and muddato_app is a
        # member of muddato_runtime — so the login CAN become the group role.
        # That is not a hole and the assertion should not pretend it is refused:
        # policies are written `TO muddato_app`, and muddato_runtime is the
        # group, not a member of it, so it matches none of them. What must hold
        # is that becoming it yields no tenant row.
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL ROLE muddato_runtime"))
            assert conn.execute(text("SELECT count(*) FROM customers")).scalar() == 0
    finally:
        engine.dispose()


def test_downgrade_and_reupgrade_remain_explicit(pre_cutover):
    """The rollback still works, still reopens what it says, and still returns."""
    owner = pre_cutover
    before = _tenant_fingerprint(owner)

    down = _run(["-m", "alembic", "downgrade", "rf0failclosed"],
                {"MIGRATION_DATABASE_URL_SYNC": owner})
    assert down.returncode == 0, down.stderr[-1500:]
    identity_policies = _sql(owner,
        "SELECT policyname FROM pg_policies WHERE tablename='users' "
        "AND 'muddato_identity' = ANY(roles)")
    assert identity_policies, (
        "rg0's downgrade claims to restore directory-wide identity access; it "
        "must actually do so, or the documented rollback risk is not the real one"
    )

    up = _alembic(owner, "head")
    assert up.returncode == 0, up.stderr[-1500:]
    assert _sql(owner,
        "SELECT policyname FROM pg_policies WHERE tablename='users' "
        "AND 'muddato_identity' = ANY(roles)") == []
    assert _tenant_fingerprint(owner) == before, "the round trip changed tenant data"


# ── 7 · the tool's own security properties ───────────────────────────────────

def test_the_bootstrap_tool_never_imports_the_application_settings():
    """The whole reason it can run pre-cutover.

    Checked by import, not by reading the source: a transitive import through
    any helper would reintroduce the deadlock just as effectively as a direct one.
    """
    result = _run(
        ["-c",
         "import scripts.bootstrap_runtime_login, sys; "
         "print('app.config' in sys.modules)"],
        {},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", (
        "the bootstrap tool imports app.config — it will deadlock in exactly the "
        "posture it exists to repair"
    )


def test_the_bootstrap_tool_refuses_a_url_or_password_on_the_command_line():
    """`argv` is visible in `ps`, in shell history and in supervisor logs."""
    result = _run(["-m", "scripts.bootstrap_runtime_login", "--help"], {})
    assert result.returncode == 0
    for forbidden in ("--migration-url", "--password", "--url", "--pw"):
        assert forbidden not in result.stdout, (
            f"{forbidden} would put a credential in argv"
        )
    # argparse re-wraps the epilog, so compare on collapsed whitespace.
    assert "never from the command line" in " ".join(result.stdout.split()).lower()


def test_the_bootstrap_tool_fails_clearly_with_no_url():
    # merge=False: this process's own environment may already carry
    # MUDDATO_BOOTSTRAP_MIGRATION_URL (the `integration` job's env: block sets
    # it for the whole job), and _run's default merge only overlays keys — it
    # cannot unset one, so the child would inherit it and this test would
    # observe the "set" behaviour while asserting on the "unset" one.
    result = _run(
        ["-m", "scripts.bootstrap_runtime_login"], {"PATH": os.environ["PATH"]}, merge=False,
    )
    assert result.returncode == 2
    assert "MUDDATO_BOOTSTRAP_MIGRATION_URL is not set" in result.stderr


def test_the_bootstrap_skips_authority_roles_that_do_not_exist_yet():
    """The pre-rg0 branch, proved on the statement list itself.

    At `rf0failclosed` the cluster has five authority roles; `muddato_webhook`
    arrives with `rg0credsplit`. `GRANT` on a role that does not exist is a hard
    error, so a bootstrap that grants blindly would abort halfway through an
    otherwise-correct run — leaving a login with attributes set and memberships
    missing, which is the worst of both.

    Asserted here rather than in the rehearsal above because roles are
    cluster-scoped: no test can reliably arrange for `muddato_webhook` to be
    absent on a machine that has migrated anything to rg0.
    """
    from scripts.bootstrap_runtime_login import _statements

    at_rf0 = {r for r in AUTHORITY_ROLES if r != "muddato_webhook"}
    stmts = _statements(at_rf0)
    granted = {s.split()[1] for s in stmts if s.startswith("GRANT muddato_")}
    assert granted == at_rf0
    assert not any("GRANT muddato_webhook" in s for s in stmts)

    # And after the migration, the full set.
    assert {
        s.split()[1] for s in _statements(set(AUTHORITY_ROLES))
        if s.startswith("GRANT muddato_")
    } == set(AUTHORITY_ROLES)


def test_the_scram_verifier_is_correct_and_deterministic():
    """The password never crosses the wire, so the verifier must be right.

    A wrong verifier is the worst failure mode available here: the bootstrap
    reports success, and the cutover fails at the only moment nobody can debug
    it — when the new release cannot authenticate and the old one is gone. Pinned
    against a fixed salt so the algorithm cannot drift, and round-tripped through
    a real server in the rehearsal above, which is the assertion that matters.
    """
    from scripts.bootstrap_runtime_login import scram_verifier

    fixed = bytes(range(16))
    once = scram_verifier("hunter2", salt=fixed, iterations=4096)
    twice = scram_verifier("hunter2", salt=fixed, iterations=4096)
    assert once == twice
    assert once.startswith("SCRAM-SHA-256$4096:")
    assert once.count("$") == 2 and once.count(":") == 2
    assert scram_verifier("hunter3", salt=fixed) != once
    # The plaintext must not be recoverable from, or present in, the verifier.
    assert "hunter2" not in once

    with pytest.raises(ValueError, match="ASCII"):
        scram_verifier("pärola", salt=fixed)
