"""The production preflight, against a real database and against bad postures.

Two halves, because the command has two jobs. The first is to describe a healthy
database accurately (and to say nothing it must not say — no row data, no
identifiers, no credentials). The second is to REFUSE every forbidden posture,
which is checked by feeding the judgement a report that carries each one, rather
than by breaking the shared test cluster in six different ways.

Rewritten by the Batch 5 independent-review correction. The largest change is
one deletion: this file used to contain a class asserting that a BYPASSRLS
connection role is "recorded as a risk and does not block", on the reasoning
that a managed platform hands you the login it wants to hand you. That reasoning
was the finding. The answer is to stop connecting as that role — the application
now authenticates as ``muddato_api``, which owns nothing and bypasses nothing —
so a BYPASSRLS runtime connection is a deploy blocker and is asserted as one
below.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from app.cli.rls_preflight import Report, _judge, collect, render
from app.core.rls_manifest import AUTHORITY_ROLES, CAPABILITY_FUNCTIONS, ROLE_API_LOGIN
from tests import _dbcreds

#: Only the DB-backed tests are async; the judgement tests are pure functions,
#: so the loop scope is declared per test rather than for the module.
_module_loop = pytest.mark.asyncio(loop_scope="module")


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "+psycopg")


def _healthy() -> Report:
    """The posture a correct deployment reports, as facts.

    Every field here is something the correction made load-bearing: the runtime
    login is the expected one, can log in, is NOINHERIT, owns nothing, cannot
    create, is refused on a raw connection, holds exactly the authority
    memberships and no others — and the migration identity is a different login
    that owns the schema.
    """
    r = Report(
        database="muddato", server="db:5432",
        current_user=ROLE_API_LOGIN, session_user=ROLE_API_LOGIN,
        expected_runtime_role=ROLE_API_LOGIN,
        rolsuper=False, rolbypassrls=False, rolcanlogin=True, rolinherit=False,
        owns_protected_tables=0, can_create_in_public=False,
        can_alter_protected_tables=False,
        raw_probed_tables=48, raw_refused_tables=48,
        raw_probed_sequences=0, raw_refused_sequences=0,
        alembic_head="rg0credsplit", expected_head="rg0credsplit",
        migration_checked=True, migration_user="postgres",
        migration_is_separate=True, migration_owns_tables=48,
        same_database=True,
    )
    # Both connections attached to the same database — the identity the
    # preflight compares, not the URL.
    r.runtime_db = {
        "name": "postgres", "oid": "5", "cluster": "7", "cluster_source": "system_identifier",
    }
    r.migration_db = dict(r.runtime_db)
    r.login_memberships = sorted(AUTHORITY_ROLES)
    r.roles = {
        name: {"rolsuper": False, "rolbypassrls": False,
               "rolcanlogin": False, "rolinherit": True}
        for name in ("muddato_runtime", *AUTHORITY_ROLES)
    }
    # Assuming any authority role drops both privileges — defence in depth
    # behind the credential, not the defence itself.
    r.effective_after_downgrade = dict.fromkeys(AUTHORITY_ROLES, (False, False))
    r.capability_functions_ok = len(CAPABILITY_FUNCTIONS)
    return r


# ── against the real database ────────────────────────────────────────────────

def _collect_as_runtime() -> Report:
    """Collect the posture through the RESTRICTED login, as production does.

    Collecting as the schema owner would report a posture nobody runs: the owner
    can read every table, so the leak probes would be measuring the wrong
    identity entirely.
    """
    url = _dbcreds.sync(_dbcreds.runtime_url())
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return collect(conn, url=url, expected_runtime_role=ROLE_API_LOGIN)
    finally:
        engine.dispose()


def _collect_migration_into(report: Report) -> None:
    """Add the migration-identity facts, which live on the other connection."""
    from app.cli.rls_preflight import collect_migration

    url = _dbcreds.sync(_dbcreds.migration_url())
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            collect_migration(
                conn, report, url=url, runtime_user=report.current_user,
            )
    finally:
        engine.dispose()


@_module_loop
async def test_a_correctly_migrated_database_passes(rls_world) -> None:
    from app.cli.rls_preflight import check_webhook_routes

    report = _collect_as_runtime()
    _collect_migration_into(report)
    check_webhook_routes(report)
    _judge(report)

    assert report.failures == [], render(report)
    # The two facts the correction is ABOUT, asserted explicitly rather than
    # left implicit in "no failures".
    assert report.raw_probed_tables > 30, "the bare-login sweep did not run"
    assert report.raw_refused_tables == report.raw_probed_tables
    assert report.raw_permitted_tables == [] and report.raw_leaked_tables == []
    assert report.direct_table_grants == [] and report.direct_sequence_grants == []
    assert report.migration_is_separate is True
    assert report.same_database is True
    assert report.fail_open_policies == []
    assert report.unclassified_tables == []
    assert report.leaking_tables == []
    assert report.missing_with_check == []
    assert report.force_rls_tables == 0
    assert report.owned_by_runtime_roles == 0
    assert report.alembic_head == report.expected_head


@_module_loop
async def test_the_empirical_probe_is_not_vacuous(rls_world) -> None:
    """The probe reads a table that HAS rows on both sides.

    A leak check against an empty table passes for the wrong reason, which is
    how the original fail-open policies survived a green suite for months.
    """
    from app.cli.rls_preflight import PROTECTED_TABLES

    assert "customers" in PROTECTED_TABLES
    async with rls_world.engine.connect() as conn:
        from sqlalchemy import text

        seeded = (await conn.execute(
            text("SELECT count(*) FROM customers"))).scalar_one()
        await conn.rollback()
    assert seeded >= 2, "the preflight probe would be vacuous on an empty table"


@_module_loop
async def test_the_report_never_prints_row_data_or_credentials(rls_world) -> None:
    report = _collect_as_runtime()
    _collect_migration_into(report)
    text_out = render(report)

    forbidden = (
        rls_world.tenant_a, rls_world.customer_a, rls_world.payment_a,
        rls_world.invitation_verifier_a, "+99890", "Alice", "Bob",
        str(rls_world.tg_customer), "password", "secret",
    )
    for needle in forbidden:
        assert needle not in text_out, f"the preflight printed {needle!r}"
    assert "muddato_np@" not in text_out
    # Read off the world's own URL, not written down: CI runs Postgres on a
    # non-default host port (55432), and a hardcoded `localhost:5432` asserted
    # the developer's machine rather than the property — that the server is
    # still named at all once the credentials are stripped out.
    from sqlalchemy.engine import make_url

    where = make_url(_dbcreds.render(rls_world.engine.url))
    assert f"{where.host}:{where.port}" in text_out, (
        "the server should still be identifiable")


# ── every forbidden posture must fail ────────────────────────────────────────

def test_a_healthy_report_passes() -> None:
    r = _healthy()
    _judge(r)
    assert r.failures == []


@pytest.mark.parametrize("mutate,expected", [
    (lambda r: setattr(r, "rolsuper", True), "SUPERUSER"),
    # No longer a tolerated managed-platform default — see the module docstring.
    (lambda r: setattr(r, "rolbypassrls", True), "BYPASSRLS"),
    (lambda r: setattr(r, "rolinherit", True), "NOINHERIT"),
    (lambda r: setattr(r, "rolcanlogin", False), "cannot LOGIN"),
    (lambda r: setattr(r, "current_user", "postgres"), "expected"),
    (lambda r: setattr(r, "can_create_in_public", True), "CREATE in schema public"),
    (lambda r: setattr(r, "can_alter_protected_tables", True), "ALTER or DROP"),
    (lambda r: r.raw_leaked_tables.append("payments"), "READ ROWS"),
    (lambda r: r.raw_permitted_tables.append("payments"), "PERMITTED"),
    (lambda r: r.raw_permitted_sequences.append("invoice_seq"), "sequence"),
    (lambda r: setattr(r, "raw_probed_tables", 0), "no protected table was probed"),
    (lambda r: r.direct_table_grants.append("payments:SELECT"),
     "direct table privilege"),
    (lambda r: r.direct_sequence_grants.append("invoice_seq:USAGE"),
     "direct sequence privilege"),
    (lambda r: r.default_privilege_grants.append("r:SELECT"),
     "ALTER DEFAULT PRIVILEGES"),
    (lambda r: r.unexpected_reachable_roles.append("sneaky"), "transitively"),
    (lambda r: r.owned_tables.append("payments"), "OWNS"),
    # Same login, different database — a head-equivalent clone.
    (lambda r: r.migration_db.__setitem__("oid", "999"), "DIFFERENT databases"),
    (lambda r: r.migration_db.__setitem__("name", "other"), "database identity"),
    (lambda r: r.migration_db.__setitem__("cluster", "other"), "different PostgreSQL clusters"),
    (lambda r: r.migration_db.__setitem__("oid", None), "could not be established"),
    (lambda r: r.unexpected_memberships.append("postgres"), "should not"),
    (lambda r: r.missing_memberships.append("muddato_webhook"), "cannot assume"),
    (lambda r: setattr(r, "migration_checked", False), "was not checked"),
    (lambda r: setattr(r, "migration_is_separate", False), "SAME"),
    (lambda r: setattr(r, "migration_owns_tables", 0), "owns no table"),
    (lambda r: r.identity_users_policies.append("rls_users_identity"),
     "identity authority has a policy on 'users'"),
    (lambda r: setattr(r, "identity_users_rows", 3), "READ the users directory"),
    (lambda r: r.capability_problems.append("rls_x(): search_path is not pinned"),
     "search_path"),
    (lambda r: r.webhook_routes_with_authority.append("/api/webhooks/click/prepare"),
     "router-level cross-tenant authority"),
    (lambda r: setattr(r, "owned_by_runtime_roles", 3), "OWNED by an authority role"),
    (lambda r: r.fail_open_policies.append("customers.x"), "fail-open"),
    (lambda r: r.missing_with_check.append("payments.x"), "WITH CHECK"),
    (lambda r: r.missing_policies.append("kyc_blobs"), "no tenant-authority policy"),
    (lambda r: r.unclassified_tables.append("brand_new"), "absent from the RLS manifest"),
    (lambda r: r.leaking_tables.append("muddato_app:customers"), "unscoped read"),
    (lambda r: setattr(r, "alembic_head", "rd0invverifier"), "migration head"),
    (lambda r: setattr(r, "expected_head", None), "exactly one head"),
    (lambda r: r.roles.__setitem__("muddato_app", {
        "rolsuper": True, "rolbypassrls": False,
        "rolcanlogin": False, "rolinherit": True}), "muddato_app is a SUPERUSER"),
    (lambda r: r.roles.__setitem__("muddato_worker", {
        "rolsuper": False, "rolbypassrls": True,
        "rolcanlogin": False, "rolinherit": True}), "muddato_worker has BYPASSRLS"),
    (lambda r: r.roles.__setitem__("muddato_customer", {
        "rolsuper": False, "rolbypassrls": False,
        "rolcanlogin": True, "rolinherit": True}), "can LOGIN"),
    (lambda r: r.roles.__setitem__("muddato_identity", {
        "rolsuper": False, "rolbypassrls": False,
        "rolcanlogin": False, "rolinherit": False}), "NOINHERIT"),
    (lambda r: r.roles.pop("muddato_platform"), "does not exist"),
])
def test_each_forbidden_posture_is_refused(mutate, expected) -> None:
    r = _healthy()
    mutate(r)
    _judge(r)
    assert any(expected in f for f in r.failures), (
        f"expected a failure mentioning {expected!r}, got {r.failures}"
    )


# ── the privilege downgrade, still checked (defence in depth) ────────────────

class TestAssumingAnAuthorityStripsPrivilege:
    """PostgreSQL decides row-security bypass from the CURRENT role, so an
    authority role that kept SUPERUSER or BYPASSRLS would make every policy
    decorative. This is no longer the ONLY thing standing between a request and
    the data — the credential is — but it is still a property that must hold,
    and silence about it is not evidence."""

    def test_a_superuser_connection_is_still_fatal(self) -> None:
        r = _healthy()
        r.rolsuper = True
        _judge(r)
        assert any("SUPERUSER" in f for f in r.failures)

    @pytest.mark.parametrize("attrs,expected", [
        ((False, True), "does not drop BYPASSRLS"),
        ((True, False), "does not drop SUPERUSER"),
    ])
    def test_a_downgrade_that_does_not_strip_privilege_is_fatal(
        self, attrs, expected,
    ) -> None:
        r = _healthy()
        r.effective_after_downgrade["muddato_app"] = attrs
        _judge(r)
        assert any(expected in f for f in r.failures), r.failures

    def test_an_unverifiable_downgrade_is_fatal(self) -> None:
        """Silence is not evidence: if the probe could not run, the property is
        unproven and the deploy must stop."""
        r = _healthy()
        r.effective_after_downgrade.pop("muddato_worker")
        _judge(r)
        assert any("could not verify" in f for f in r.failures)


def test_the_command_exits_non_zero_when_it_fails(rls_world) -> None:
    """The exit code is the whole contract with the deploy pipeline.

    Driven end to end with BOTH credentials, because that is how the release
    command invokes it: the runtime URL is what the application uses and the
    migration URL is what Alembic uses, and the command's job is to prove they
    are different and each correct.
    """
    from app.cli import rls_preflight

    runtime = _dbcreds.sync(_dbcreds.runtime_url())
    migration = _dbcreds.sync(_dbcreds.migration_url())
    argv = ["--url", runtime, "--migration-url", migration]
    assert rls_preflight.main(argv) == 0

    original = rls_preflight.collect

    def broken(conn, *, url="", expected_runtime_role=""):
        r = original(conn, url=url, expected_runtime_role=expected_runtime_role)
        r.fail_open_policies.append("customers.pretend")
        return r

    rls_preflight.collect = broken  # type: ignore[assignment]
    try:
        assert rls_preflight.main(argv) == 1
    finally:
        rls_preflight.collect = original  # type: ignore[assignment]


def test_the_command_fails_when_the_two_credentials_are_one(rls_world) -> None:
    """Pointing both URLs at the schema owner — the pre-correction posture —
    must fail the release, not merely warn."""
    from app.cli import rls_preflight

    owner = _dbcreds.sync(_dbcreds.migration_url())
    assert rls_preflight.main(["--url", owner, "--migration-url", owner]) == 1


@_module_loop
async def test_the_inventory_is_complete_and_prints_no_data(rls_world) -> None:
    """``--inventory`` is the Part-1 deliverable, so it has to be complete AND
    safe to run against production."""
    from app.cli.rls_preflight import inventory
    from app.core.rls_manifest import CLASSIFICATION

    url = _sync_url(_dbcreds.render(rls_world.engine.url))
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            out = inventory(conn)
    finally:
        engine.dispose()

    for table in CLASSIFICATION:
        assert f"`{table}`" in out, f"the inventory omits {table}"
    assert "UNCLASSIFIED" not in out
    # Every required Part-1 column is present.
    for column in ("tenant_id", "nullable", "RLS", "FORCE", "owner", "category",
                   "USING", "WITH CHECK", "cmd", "roles"):
        assert column in out, f"the inventory has no {column} column"
    # …and none of the data the world is seeded with.
    for needle in (rls_world.customer_a, rls_world.payment_a, "Alice", "Bob",
                   "+99890", rls_world.invitation_verifier_a,
                   str(rls_world.tg_customer)):
        assert needle not in out, f"the inventory printed {needle!r}"


@_module_loop
async def test_the_shared_catalogue_is_not_reported_as_a_leak(rls_world) -> None:
    """A `tenant_global_read` table legitimately exposes its `tenant_id IS NULL`
    rows to every shop — that IS the shared product catalogue.

    THIS FAILED IN PRODUCTION. The first manual deploy aborted on
    `unscoped read returned rows on: muddato_app:catalog_models`, because the
    probe asked for ANY row and production has thousands of catalogue rows. The
    local database had an empty catalogue, so it never fired — the same
    "an empty table proves nothing" trap this suite exists to close.

    What must not leak from such a table is a TENANT-OWNED row, which is what the
    probe now asks for.
    """
    from sqlalchemy import text as _text

    from app.cli.rls_preflight import _leak_probe

    assert "tenant_id IS NOT NULL" in _leak_probe("catalog_models"), (
        "the shared-catalogue table must be probed for tenant-owned rows only"
    )
    assert "tenant_id IS NOT NULL" not in _leak_probe("customers"), (
        "every other protected table must still be probed for ANY row"
    )

    # And against the real database, with both row shapes present.

    async with rls_world.engine.begin() as conn:
        brand = (await conn.execute(_text(
            "INSERT INTO catalog_brands (id, slug, name, category_ids, created_at, "
            "updated_at) VALUES (gen_random_uuid(), 'pf-b', 'B', '{}', now(), now()) "
            "RETURNING id"))).scalar_one()
        await conn.execute(_text(
            "INSERT INTO catalog_models (id, brand_id, slug, name, tenant_id, "
            "status, created_at, updated_at) VALUES "
            "(gen_random_uuid(), :b, 'pf-g', 'Global', NULL, 'global', now(), now()), "
            "(gen_random_uuid(), :b, 'pf-o', 'Owned', :t, 'global', now(), now())"),
            {"b": brand, "t": rls_world.tenant_a})
    try:
        url = _sync_url(_dbcreds.render(rls_world.engine.url))
        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                report = collect(conn, url=url)
        finally:
            engine.dispose()
        assert report.leaking_tables == [], report.leaking_tables
    finally:
        async with rls_world.engine.begin() as conn:
            await conn.execute(_text(
                "DELETE FROM catalog_models WHERE slug IN ('pf-g','pf-o')"))
            await conn.execute(_text("DELETE FROM catalog_brands WHERE slug = 'pf-b'"))


def test_the_deploy_workflow_runs_the_preflight() -> None:
    """The check is only worth anything if the pipeline actually runs it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    hits = [
        p for p in (root / ".github" / "workflows").glob("*.yml")
        if "app.cli.rls_preflight" in p.read_text()
    ]
    assert hits, (
        "no workflow invokes `python -m app.cli.rls_preflight` — a preflight "
        "nothing runs is documentation, not a gate"
    )


# ── an UNREVIEWED SECURITY DEFINER function must fail the deploy ─────────────

@_module_loop
async def test_an_unclassified_security_definer_function_is_refused(rls_world) -> None:
    """The same rule tables have, applied to definer functions.

    A table absent from the manifest fails the preflight — that is what stops a
    new tenant table shipping without a policy. A ``SECURITY DEFINER`` function
    had no equivalent: it runs as its OWNER, which makes it a
    privilege-escalation primitive by construction, and one could be added with
    an unpinned ``search_path`` or ``PUBLIC`` EXECUTE without the preflight
    saying a word.

    Found by review rather than by theory: ``next_invoice_sequence`` was already
    living outside the manifest, correct but unchecked.
    """
    from sqlalchemy import text as _text

    owner = create_engine(_dbcreds.sync(_dbcreds.migration_url()),
                          isolation_level="AUTOCOMMIT")
    try:
        clean = _collect_as_runtime()
        _collect_migration_into(clean)
        _judge(clean)
        assert clean.unclassified_definer_functions == []
        assert clean.failures == [], clean.failures

        with owner.connect() as conn:
            conn.execute(_text(
                "CREATE OR REPLACE FUNCTION public.rls_probe_unreviewed() "
                "RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$"
            ))
        mutated = _collect_as_runtime()
        _collect_migration_into(mutated)
        _judge(mutated)
        assert "rls_probe_unreviewed()" in mutated.unclassified_definer_functions
        assert any("absent from the capability manifest" in f
                   for f in mutated.failures), mutated.failures
    finally:
        with owner.connect() as conn:
            conn.execute(_text("DROP FUNCTION IF EXISTS public.rls_probe_unreviewed()"))
        owner.dispose()

    restored = _collect_as_runtime()
    _collect_migration_into(restored)
    _judge(restored)
    assert restored.failures == [], restored.failures


# ── the event-trigger definer bucket, against a real database ────────────────
#
# This bucket decides whether a SECURITY DEFINER function escapes the capability
# allowlist, and none of it is exercised by the migrated schema: the one live
# entry (``rls_auto_enable``) is provisioned OUT OF BAND on production and is
# deliberately not created by a migration, so on any test cluster the accept
# path is dead. These tests build the objects the production cluster has, then
# each way of faking them, and assert the deploy still refuses.

_EVT_FN = "rls_test_event_definer"
_EVT_SIG = f"{_EVT_FN}()"
_EVT_TRG = "rls_test_ensure"


def _evt_probe(migration_conn, *, manifest_entries=(_EVT_SIG,)):
    """Run the capability collection with ``manifest_entries`` classified.

    Collected on the MIGRATION connection because that identity can see every
    function; the bucket is a property of the schema, not of who is asking.
    """
    from app.cli import rls_preflight

    original = rls_preflight.EVENT_TRIGGER_FUNCTIONS
    rls_preflight.EVENT_TRIGGER_FUNCTIONS = frozenset(manifest_entries)
    try:
        r = Report()
        rls_preflight._collect_capabilities(migration_conn, r)
        return r
    finally:
        rls_preflight.EVENT_TRIGGER_FUNCTIONS = original


@pytest.fixture
def evt_conn(rls_world):
    """A migration-credential connection that can create an event trigger.

    Takes ``rls_world`` for the same reason every other DB-backed test in this
    file does: it is what skips the tier when ``TEST_DATABASE_URL`` is unset, and
    it is what guarantees a migrated schema underneath.

    ``CREATE EVENT TRIGGER`` is additionally superuser-only. That holds on the CI
    Postgres service and on a local cluster; it does NOT hold on the managed
    production platform, where the schema owner is not a superuser. Skipping is
    therefore about the CLUSTER, not about the test being optional — and it says
    so, so a skip here can never be mistaken for a pass.
    """
    from sqlalchemy import text

    url = _dbcreds.sync(_dbcreds.migration_url())
    engine = create_engine(url)
    conn = engine.connect()
    try:
        if not conn.execute(
            text("SELECT usesuper FROM pg_user WHERE usename = current_user")
        ).scalar():
            pytest.skip(
                "CREATE EVENT TRIGGER requires superuser; the migration identity "
                "on this cluster is not one, so the bucket cannot be exercised here"
            )
        _evt_drop(conn)
        yield conn
    finally:
        try:
            _evt_drop(conn)
            conn.commit()
        finally:
            conn.close()
            engine.dispose()


def _evt_drop(conn) -> None:
    from sqlalchemy import text

    for stmt in (
        f"DROP EVENT TRIGGER IF EXISTS {_EVT_TRG}",
        f"DROP FUNCTION IF EXISTS public.{_EVT_FN}()",
        "DROP TYPE IF EXISTS public.event_trigger",
    ):
        conn.execute(text(stmt))
    conn.commit()


def _evt_make(conn, body_sql: str, *, attach: bool = True, enabled: bool = True) -> None:
    from sqlalchemy import text

    conn.execute(text(body_sql))
    if attach:
        conn.execute(
            text(f"CREATE EVENT TRIGGER {_EVT_TRG} ON ddl_command_end "
                 f"EXECUTE FUNCTION public.{_EVT_FN}()")
        )
        if not enabled:
            conn.execute(text(f"ALTER EVENT TRIGGER {_EVT_TRG} DISABLE"))
    conn.commit()


_GOOD = f"""
CREATE FUNCTION public.{_EVT_FN}() RETURNS event_trigger
  LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog'
  AS $fn$ BEGIN END $fn$
"""


def test_a_real_event_trigger_definer_is_accepted(evt_conn) -> None:
    """The production shape: plpgsql, definer, pinned, attached and enabled."""
    _evt_make(evt_conn, _GOOD)
    r = _evt_probe(evt_conn)

    assert r.event_trigger_functions_accepted == [_EVT_SIG]
    assert r.event_trigger_problems == []
    assert _EVT_SIG not in r.unclassified_definer_functions
    _judge(r)
    assert not [f for f in r.failures if _EVT_FN in f]


@pytest.mark.parametrize("label,body,attach,enabled,expected", [
    # An ordinary callable definer wearing the name. The classification is for
    # functions nothing can call; this one anything can.
    ("ordinary definer", f"""
        CREATE FUNCTION public.{_EVT_FN}() RETURNS void
          LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog'
          AS $fn$ BEGIN END $fn$
     """, False, True, "does not return pg_catalog.event_trigger"),
    # THE REGRESSION TEST for the bypass an independent review found: a
    # `public.event_trigger` COMPOSITE TYPE needs only CREATE on public, which
    # the migration identity has, and `format_type()` renders it as the bare
    # word `event_trigger` whenever public precedes pg_catalog on the search
    # path. A text comparison accepts this function; it is fully callable.
    ("shadowed return type", f"""
        CREATE TYPE public.event_trigger AS (x int);
        CREATE FUNCTION public.{_EVT_FN}() RETURNS public.event_trigger
          LANGUAGE sql SECURITY DEFINER SET search_path TO 'pg_catalog'
          AS $fn$ SELECT ROW(1)::public.event_trigger $fn$
     """, False, True, "does not return pg_catalog.event_trigger"),
    # Right type, attached to nothing — so the "only the DDL machinery calls it"
    # premise the whole bucket rests on is simply false for it.
    ("attached to nothing", _GOOD, False, True, "backs no enabled event trigger"),
    # Attached but DISABLED: the control is gone and the catalogue still looks
    # populated. This is the drift the attachment check exists to catch.
    ("trigger disabled", _GOOD, True, False, "backs no enabled event trigger"),
    # Unpinned search_path — asserted even for a non-definer, because a
    # non-definer event-trigger function runs as whoever issued the DDL, which
    # in production is the schema owner.
    ("unpinned search_path", f"""
        CREATE FUNCTION public.{_EVT_FN}() RETURNS event_trigger
          LANGUAGE plpgsql SECURITY DEFINER AS $fn$ BEGIN END $fn$
     """, True, True, "search_path is not pinned"),
])
def test_every_way_of_faking_an_event_trigger_definer_is_refused(
    evt_conn, label, body, attach, enabled, expected,
) -> None:
    _evt_make(evt_conn, body, attach=attach, enabled=enabled)
    r = _evt_probe(evt_conn)

    assert not r.event_trigger_functions_accepted, f"{label}: accepted"
    assert any(expected in p for p in r.event_trigger_problems), (
        f"{label}: expected a problem mentioning {expected!r}, "
        f"got {r.event_trigger_problems}"
    )
    _judge(r)
    assert any(expected in f for f in r.failures), f"{label}: did not fail the deploy"


def test_a_rejected_entry_is_still_counted_as_an_unclassified_definer(evt_conn) -> None:
    """Failing its own checks must not also buy it an exemption from the sweep.

    The `void` impostor is a SECURITY DEFINER function in `public` that is not in
    `CAPABILITY_FUNCTIONS`, so it has to fail twice: once for lying about what it
    is, once for being an unreviewed definer.
    """
    _evt_make(evt_conn, f"""
        CREATE FUNCTION public.{_EVT_FN}() RETURNS void
          LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'pg_catalog'
          AS $fn$ BEGIN END $fn$
    """, attach=False)
    r = _evt_probe(evt_conn)

    assert _EVT_SIG in r.unclassified_definer_functions
    _judge(r)
    assert any("absent from the capability manifest" in f for f in r.failures)
    assert any("does not return pg_catalog.event_trigger" in f for f in r.failures)


def test_an_absent_entry_is_reported_but_never_fails_the_deploy(evt_conn) -> None:
    """Dev, CI and a fresh project legitimately lack it — but the report says so.

    Absence and "somebody dropped the production control" are indistinguishable
    otherwise, and nobody diffs preflight output between releases.
    """
    r = _evt_probe(evt_conn, manifest_entries=("rls_definitely_not_here()",))

    assert r.event_trigger_functions_absent == ["rls_definitely_not_here()"]
    assert r.event_trigger_problems == []
    _judge(r)
    assert not [f for f in r.failures if "rls_definitely_not_here" in f]
    assert "event-trigger absent" in render(r)
