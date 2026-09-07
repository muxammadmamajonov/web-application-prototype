"""Read-only production preflight for the database authorization posture.

    python -m app.cli.rls_preflight              # runtime + migration + code posture
    python -m app.cli.rls_preflight --json       # machine-readable
    python -m app.cli.rls_preflight --inventory  # the per-table RLS inventory

Run it in the deploy pipeline AFTER migrations and BEFORE public traffic. It
answers one question — *is the database in the posture the application assumes?*
— and exits non-zero when it is not, so a drifted cluster fails the deploy
instead of quietly serving one tenant's data to another.

FOUR POSTURES, CHECKED SEPARATELY
---------------------------------
**Application.** Connects with ``DATABASE_URL_SYNC`` — the ordinary runtime
login — and proves what that login is and is not: LOGIN, NOSUPERUSER,
NOBYPASSRLS, NOINHERIT, owner of nothing, no ``CREATE`` on ``public``, no
membership beyond the authority roles, and — measured, not assumed — unable to
read a protected table on a raw connection or under any tenant-facing authority
with no scope.

**Migration.** Connects with ``MIGRATION_DATABASE_URL_SYNC`` and proves it is a
DIFFERENT login, that it owns the schema, and that it is not what the
application is configured to use. It also reads the Alembic head, because the
runtime login deliberately has no privilege on ``alembic_version``.

**Webhook.** A code check, not a database one: no public provider webhook route
may carry a router-level worker authority. This is the defect that let an
unauthenticated request hold cross-tenant reach before its signature was checked,
and it is invisible to any amount of catalogue inspection.

**Identity.** The pre-authentication authority must have no policy on ``users``
at all, and the narrow capability functions must have the right owner, a pinned
``search_path``, and EXECUTE granted to exactly one role with PUBLIC revoked.

WHY IT EXISTS AT ALL
--------------------
The isolation guarantee lives in properties of the DATABASE and of the DEPLOY
CONFIGURATION, not of the repository: which roles exist, what attributes they
carry, who owns the tables, which policies are attached, and which credential
each process is handed. A migration cannot fix all of them — ``ALTER ROLE …
NOSUPERUSER`` needs a superuser, and the deployment role deliberately is not one
— and a managed platform can hand back a restored cluster with different role
attributes than the one it replaced. The migration warns in those cases. This is
what refuses to serve traffic.

A SCHEMA-OWNER OR BYPASSRLS RUNTIME CONNECTION IS NO LONGER A TOLERATED RISK.
The previous version of this file recorded it as a non-blocking
"managed-platform default" and relied on a per-transaction ``SET LOCAL ROLE`` to
compensate. That made every connection opened outside SQLAlchemy's session
machinery an unmonitored bypass. It is now a hard failure — permitted only when
this tool is deliberately evaluating the MIGRATION identity as the migration
identity.

WHAT IT NEVER PRINTS. No row data, tenant names, customer identifiers, phone
numbers, file ids, Telegram ids, tokens, secrets or connection strings. Only
catalogue facts and counts. Hosts are reported as ``host:port`` from the URL so
an operator can tell WHICH database answered, with credentials stripped.

THE EMPIRICAL CHECK. Structural analysis of policy text is necessary but not
sufficient — a policy can read correctly and still grant, and a table can be
covered by a *second* permissive policy that ORs the first one open. So the
central check actually runs the query: it assumes each tenant-facing role with
no scope set and asks each protected table for a single row, and it asks again
with no role assumed at all. Every probe runs inside a transaction that is
rolled back, and every statement is a SELECT, so it is safe against production.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from app.core.rls_manifest import (
    AUTHORITY_ROLES,
    CAPABILITY_FUNCTIONS,
    CLASSIFICATION,
    EVENT_TRIGGER_FUNCTIONS,
    EXPECTED_PATHS,
    ROLE_IDENTITY,
    ROLE_PLATFORM,
    ROLE_RUNTIME,
    ROLE_WORKER,
    TENANT_SCOPED_CATEGORIES,
    Category,
)

#: Roles an ordinary request can run as. These must never see a tenant row
#: without a scope; ``muddato_worker``/``muddato_platform`` legitimately can,
#: which is why they are excluded here and checked for a different property.
#: ``muddato_webhook`` holds no table grant at all, so its probes are expected
#: to be REFUSED rather than merely empty — which the probe loop records.
TENANT_FACING_ROLES: tuple[str, ...] = tuple(
    r for r in AUTHORITY_ROLES if r not in (ROLE_WORKER, ROLE_PLATFORM)
)

#: Tables whose rows an unscoped tenant-facing session must not be able to read.
PROTECTED_TABLES: tuple[str, ...] = tuple(sorted(
    t for t, c in CLASSIFICATION.items() if c in TENANT_SCOPED_CATEGORIES
))

#: A stable, always-populated table used for the raw-connection probe. It has to
#: be one that exists on every deployment; ``users`` is created by the first
#: migration and is never empty on a live system.
_RAW_PROBE_TABLE = "users"


def _leak_probe(table: str) -> str:
    """The "can an unscoped session read this" query, per category.

    `tenant_global_read` tables hold a SHARED catalogue in their
    `tenant_id IS NULL` rows — the global brand/model list every shop reads, and
    reads deliberately. Asking those tables for *any* row therefore reports the
    product working as designed as though it were a leak, which is exactly what
    happened on the first production run: the local database had an empty
    catalogue, so the probe never fired, and production has thousands of rows.

    What must not leak from them is a TENANT-OWNED row, so that is what is asked.
    Every other protected table must return nothing at all.
    """
    if CLASSIFICATION.get(table) is Category.TENANT_GLOBAL_READ:
        return f"SELECT 1 FROM {table} WHERE tenant_id IS NOT NULL LIMIT 1"
    return f"SELECT 1 FROM {table} LIMIT 1"


@dataclass
class Report:
    """The application posture, as facts and counts. Rendered; never interpreted twice."""

    database: str = ""
    server: str = ""
    current_user: str = ""
    session_user: str = ""
    rolsuper: bool = False
    rolbypassrls: bool = False
    rolcanlogin: bool = False
    rolinherit: bool = True
    expected_runtime_role: str = ""
    #: Memberships the login actually holds. Must be a subset of AUTHORITY_ROLES.
    login_memberships: list[str] = field(default_factory=list)
    unexpected_memberships: list[str] = field(default_factory=list)
    missing_memberships: list[str] = field(default_factory=list)
    #: True when the login can CREATE objects in schema `public`.
    can_create_in_public: bool = False
    #: True when the login is (or is a member of) the role that owns the tables.
    can_alter_protected_tables: bool = False
    owns_protected_tables: int = 0
    #: Names, not just a count — an operator needs to know WHICH.
    owned_tables: list[str] = field(default_factory=list)

    # ── the BARE-LOGIN posture ────────────────────────────────────────────
    # Everything below is measured with no `SET ROLE` and no GUC: exactly what a
    # health check, a stray `engine.connect()` or a third-party pool sees.
    #
    # Two independent methods, because each misses what the other catches. The
    # catalogue is authoritative about GRANTS (and is the only read-only way to
    # see INSERT/UPDATE/DELETE, which cannot be probed without writing); the
    # empirical probe is the only thing that notices a grant arriving by a route
    # the catalogue query did not think to look at.
    #: How many protected tables were probed with a raw SELECT.
    raw_probed_tables: int = 0
    #: …and how many were REFUSED. Refused is the target; "zero rows" is not.
    raw_refused_tables: int = 0
    #: Tables a raw SELECT was PERMITTED on, whether or not it returned rows.
    raw_permitted_tables: list[str] = field(default_factory=list)
    #: Tables a raw SELECT actually returned rows from. A strict subset of the
    #: above, kept separate so "permitted but empty" cannot read as safe.
    raw_leaked_tables: list[str] = field(default_factory=list)
    #: Sequences probed / refused, same contract.
    raw_probed_sequences: int = 0
    raw_refused_sequences: int = 0
    raw_permitted_sequences: list[str] = field(default_factory=list)
    #: ``table:PRIVILEGE`` for every privilege granted DIRECTLY to the runtime
    #: login or to PUBLIC. With a NOINHERIT login these are precisely the
    #: privileges a bare connection holds.
    direct_table_grants: list[str] = field(default_factory=list)
    direct_sequence_grants: list[str] = field(default_factory=list)
    #: ``ALTER DEFAULT PRIVILEGES`` entries that would grant future objects to it.
    default_privilege_grants: list[str] = field(default_factory=list)
    #: Every role the login can reach, transitively. Allowed: the authority
    #: roles plus the group role they inherit from. Anything else is a bypass
    #: route somebody added.
    reachable_roles: list[str] = field(default_factory=list)
    unexpected_reachable_roles: list[str] = field(default_factory=list)

    # ── are both credentials looking at the SAME database? ────────────────
    runtime_db: dict[str, Any] = field(default_factory=dict)
    migration_db: dict[str, Any] = field(default_factory=dict)
    same_database: bool | None = None
    database_identity_problems: list[str] = field(default_factory=list)

    alembic_head: str | None = None
    expected_head: str | None = None
    roles: dict[str, dict[str, Any]] = field(default_factory=dict)
    protected_tables: int = 0
    rls_enabled_tables: int = 0
    force_rls_tables: int = 0
    owned_by_runtime_roles: int = 0
    classified_policies: int = 0
    fail_open_policies: list[str] = field(default_factory=list)
    missing_with_check: list[str] = field(default_factory=list)
    unclassified_tables: list[str] = field(default_factory=list)
    leaking_tables: list[str] = field(default_factory=list)
    #: How many probes were REFUSED outright (a revoked table, which is the
    #: intended posture) — reported so "no rows" is distinguishable from
    #: "no access", which are very different reasons for an empty result.
    refused_probes: int = 0
    missing_policies: list[str] = field(default_factory=list)
    #: role → (rolsuper, rolbypassrls) AFTER `SET LOCAL ROLE`.
    effective_after_downgrade: dict[str, tuple[bool, bool]] = field(default_factory=dict)
    #: Identity posture: policies on `users` that name the pre-auth authority.
    identity_users_policies: list[str] = field(default_factory=list)
    identity_users_rows: int | None = None
    #: Capability-function posture, one entry per problem found.
    capability_problems: list[str] = field(default_factory=list)
    #: SECURITY DEFINER functions that are not in the reviewed manifest. A
    #: definer function runs as its owner, so an unreviewed one is exactly as
    #: serious as an unclassified table — and was not checked until now.
    unclassified_definer_functions: list[str] = field(default_factory=list)
    capability_functions_ok: int = 0
    #: Event-trigger definer functions found live and accepted; manifested ones
    #: absent from this cluster (reported, never a failure); and one entry per
    #: manifested event-trigger function that failed its own checks.
    event_trigger_functions_accepted: list[str] = field(default_factory=list)
    event_trigger_functions_absent: list[str] = field(default_factory=list)
    event_trigger_problems: list[str] = field(default_factory=list)
    #: Webhook posture: routes carrying a router-level worker/cross-tenant grant.
    webhook_routes_with_authority: list[str] = field(default_factory=list)
    webhook_routes_checked: int = 0
    #: Migration posture.
    migration_server: str = ""
    migration_user: str = ""
    migration_is_separate: bool = False
    migration_owns_tables: int = 0
    migration_checked: bool = False
    risks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _safe_server(url: str) -> str:
    """``host:port`` from a SQLAlchemy URL, with every credential removed."""
    try:
        from sqlalchemy.engine import make_url

        u = make_url(url)
        return f"{u.host or '?'}:{u.port or '?'}"
    except Exception:
        return "?"


def _fetch(conn, sql: str, params: dict | None = None) -> list[tuple]:
    from sqlalchemy import text

    return list(conn.execute(text(sql), params or {}).fetchall())


def _expected_head() -> str | None:
    """The single head of the local migration chain, or None if not exactly one."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
        return heads[0] if len(heads) == 1 else None
    except Exception:
        return None


def _probe(conn, sql: str, setup: tuple[str, ...] = ()) -> tuple[list[tuple] | None, str | None]:
    """Run ``sql`` in a rolled-back savepoint. Returns ``(rows, error_kind)``.

    Being REFUSED is a legitimate — often the *desired* — answer, so the error is
    returned rather than raised or swallowed. Only the exception TYPE is kept, so
    nothing a message might contain can reach the output.
    """
    from sqlalchemy import text

    trans = conn.begin_nested()
    try:
        for stmt in setup:
            conn.execute(text(stmt))
        return list(conn.execute(text(sql)).fetchall()), None
    except Exception as exc:
        return None, type(exc).__name__
    finally:
        trans.rollback()


def collect(conn, *, url: str = "", expected_runtime_role: str = "") -> Report:
    """Gather the application posture. Read-only: every probe is a SELECT, rolled back."""

    r = Report(server=_safe_server(url), expected_runtime_role=expected_runtime_role)
    #: Read from the repository, not the database: the runtime login has no
    #: privilege on `alembic_version` (by design), so the LIVE head is read on
    #: the migration connection in `collect_migration`.
    r.expected_head = _expected_head()
    (
        r.database, r.current_user, r.session_user,
        r.rolsuper, r.rolbypassrls, r.rolcanlogin, r.rolinherit,
    ) = _fetch(
        conn,
        "SELECT current_database(), current_user, session_user, "
        "       rolsuper, rolbypassrls, rolcanlogin, rolinherit "
        "FROM pg_roles WHERE rolname = current_user",
    )[0]

    # ── what the login may become ─────────────────────────────────────────
    r.login_memberships = sorted(
        name for (name,) in _fetch(
            conn,
            "SELECT g.rolname FROM pg_auth_members m "
            "JOIN pg_roles g ON g.oid = m.roleid "
            "JOIN pg_roles u ON u.oid = m.member "
            "WHERE u.rolname = current_user",
        )
    )
    allowed = set(AUTHORITY_ROLES)
    r.unexpected_memberships = sorted(set(r.login_memberships) - allowed)
    r.missing_memberships = sorted(allowed - set(r.login_memberships))

    # …and every role it can reach TRANSITIVELY. Direct memberships are not the
    # whole story: `GRANT sneaky TO muddato_app` gives the login `sneaky` without
    # adding a row that names the login, and `pg_auth_members` above would show
    # nothing. `muddato_runtime` is legitimately reachable this way — it is the
    # group the authority roles inherit their DML from — so it is the one
    # addition to the allowed set.
    r.reachable_roles = sorted(
        name for (name,) in _fetch(
            conn,
            "SELECT r.rolname FROM pg_roles r "
            " WHERE r.rolname <> current_user "
            "   AND r.rolname NOT LIKE 'pg\\_%' "
            "   AND pg_has_role(current_user, r.oid, 'MEMBER')",
        )
    )
    r.unexpected_reachable_roles = sorted(
        set(r.reachable_roles) - allowed - {ROLE_RUNTIME}
    )

    # ── what the login may CREATE / ALTER / DROP ──────────────────────────
    r.can_create_in_public = bool(_fetch(
        conn, "SELECT has_schema_privilege(current_user, 'public', 'CREATE')",
    )[0][0])
    # ALTER and DROP require OWNERSHIP (or membership in the owning role); there
    # is no separate grantable privilege to test, so ownership is the check.
    r.can_alter_protected_tables = bool(_fetch(
        conn,
        "SELECT bool_or(pg_has_role(current_user, c.relowner, 'USAGE')) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'",
    )[0][0] or False)
    r.owned_tables = sorted(
        name for (name,) in _fetch(
            conn,
            "SELECT c.relname FROM pg_class c "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S') "
            "   AND pg_get_userbyid(c.relowner) = current_user",
        )
    )
    r.owns_protected_tables = len(r.owned_tables)

    # ── role attributes ───────────────────────────────────────────────────
    for name, super_, bypass, login, inherit in _fetch(
        conn,
        "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolinherit "
        "FROM pg_roles WHERE rolname = ANY(:names)",
        {"names": list((*AUTHORITY_ROLES, ROLE_RUNTIME))},
    ):
        r.roles[name] = {
            "rolsuper": super_, "rolbypassrls": bypass,
            "rolcanlogin": login, "rolinherit": inherit,
        }

    r.owned_by_runtime_roles = _fetch(
        conn,
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
        "AND pg_get_userbyid(c.relowner) = ANY(:names)",
        {"names": list((*AUTHORITY_ROLES, ROLE_RUNTIME))},
    )[0][0]

    # ── table + policy inventory ──────────────────────────────────────────
    live_tables = {
        name for (name,) in _fetch(
            conn,
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r'",
        )
    }
    r.unclassified_tables = sorted(live_tables - set(CLASSIFICATION))
    r.protected_tables = len(PROTECTED_TABLES)
    r.rls_enabled_tables = _fetch(
        conn,
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity",
    )[0][0]
    r.force_rls_tables = _fetch(
        conn,
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relforcerowsecurity",
    )[0][0]

    policies = _fetch(
        conn,
        "SELECT tablename, policyname, cmd, roles::text, "
        "       COALESCE(qual, ''), COALESCE(with_check, '') "
        "FROM pg_policies WHERE schemaname = 'public'",
    )
    r.classified_policies = len(policies)
    for table, name, cmd, roles, qual, check in policies:
        if table == _RAW_PROBE_TABLE and ROLE_IDENTITY in roles:
            r.identity_users_policies.append(name)
        tenant_facing = any(f"{role}" in roles for role in TENANT_FACING_ROLES)
        if not tenant_facing:
            continue
        if CLASSIFICATION.get(table) not in TENANT_SCOPED_CATEGORIES:
            continue
        # The r73 shape: "no tenant named ⇒ every row".
        for clause in (qual, check):
            if "app.tenant_id" in clause and "IS NULL" in clause.upper().replace(
                "NULLIF", "",
            ):
                r.fail_open_policies.append(f"{table}.{name}")
                break
        else:
            if qual.strip().lower() == "true":
                r.fail_open_policies.append(f"{table}.{name}")
        if cmd in ("ALL", "INSERT", "UPDATE") and not check:
            r.missing_with_check.append(f"{table}.{name}")

    covered = {
        t for (t,) in _fetch(
            conn,
            "SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = 'public' "
            "AND :role = ANY (roles)",
            {"role": "muddato_app"},
        )
    }
    r.missing_policies = sorted(
        t for t in PROTECTED_TABLES if t in live_tables and t not in covered
    )

    # Which database is this connection actually attached to?
    r.runtime_db = _database_identity(conn)

    # ── the RAW-CONNECTION sweep ──────────────────────────────────────────
    #
    # The one this whole correction exists for, widened after an independent
    # review found it was probing a single table. No SET ROLE, no GUCs: exactly
    # what a health check, a stray `engine.connect()`, or a library holding its
    # own pool would do.
    #
    # It used to ask only `users`. A direct `GRANT SELECT ON payments TO
    # muddato_api` therefore slipped straight through, because the later
    # per-table probes assume an authority role FIRST and so measure the
    # authority, not the login. Every protected table and every sequence is
    # asked now.
    #
    # REFUSED is the target, not "zero rows". An empty result would mean the
    # grant exists and a policy happened to filter everything — a much weaker
    # guarantee, and one a future policy edit could silently undo.
    _collect_bare_login_privileges(conn, r, live_tables)

    # ── does assuming an authority actually drop the dangerous attributes ──
    for role in AUTHORITY_ROLES:
        if role not in r.roles:
            continue
        rows, err = _probe(
            conn,
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user",
            setup=(f"SET LOCAL ROLE {role}",),
        )
        if err is not None:
            r.risks.append(
                f"could not assume {role} to verify its privileges: {err}",
            )
        elif rows:
            r.effective_after_downgrade[role] = (bool(rows[0][0]), bool(rows[0][1]))

    # ── the per-role, per-table empirical probe ───────────────────────────
    probe_errors: list[str] = []
    for role in TENANT_FACING_ROLES:
        if role not in r.roles:
            continue
        for table in PROTECTED_TABLES:
            if table not in live_tables:
                continue
            rows, err = _probe(
                conn,
                _leak_probe(table),
                setup=(
                    f"SET LOCAL ROLE {role}",
                    "SELECT set_config('app.tenant_id', '', true)",
                    "SELECT set_config('app.user_id', '', true)",
                    "SELECT set_config('app.invitation_id', '', true)",
                ),
            )
            if err is not None:
                probe_errors.append(f"{role}:{table}:{err}")
            elif rows:
                r.leaking_tables.append(f"{role}:{table}")
    r.refused_probes = len(probe_errors)

    # ── identity posture: the pre-auth authority and the user directory ───
    if _RAW_PROBE_TABLE in live_tables and ROLE_IDENTITY in r.roles:
        rows, err = _probe(
            conn,
            f"SELECT 1 FROM {_RAW_PROBE_TABLE} LIMIT 1",
            setup=(
                f"SET LOCAL ROLE {ROLE_IDENTITY}",
                "SELECT set_config('app.user_id', '', true)",
            ),
        )
        r.identity_users_rows = None if rows is None else len(rows)

    # ── capability-function posture ───────────────────────────────────────
    _collect_capabilities(conn, r)

    # NOT judged here. The migration and webhook postures are gathered on other
    # connections (and out of the database entirely), and judging twice made the
    # first pass' "the migration credential was not checked" survive into a run
    # that had, in fact, checked it. `main` judges once, at the end, with every
    # fact in hand.
    return r


def _database_identity(conn) -> dict:
    """A logical identity for the database this connection is attached to.

    Proving the two credentials are DIFFERENT LOGINS is not the same as proving
    they are looking at the SAME DATABASE. A migration URL aimed at a clone with
    an identical Alembic head and a plausible role posture would otherwise pass
    the preflight while the application kept using a different database — the
    deploy would report success and migrate the wrong place.

    Three facts, in decreasing availability:

    ``name``    ``current_database()``. Necessary, and nowhere near sufficient
                on Supabase, where every project's database is called
                ``postgres``.
    ``oid``     the database's OID. Distinguishes two databases in one cluster.
                Also not sufficient alone: two *different* Supabase projects
                both have a ``postgres`` database, and a fresh cluster assigns
                it the same OID in both.
    ``cluster`` ``pg_control_system().system_identifier`` — unique per cluster,
                which is what separates two Supabase projects. It is not
                readable by every role on every platform, so a
                ``pg_postmaster_start_time()`` fallback stands in when it is
                refused; two independent clusters do not start in the same
                microsecond.

    Host and port are deliberately NOT part of this. Supabase's transaction
    pooler (:6543) and session/direct endpoint (:5432) are different hosts and
    ports addressing the same database, and the runtime and migration URLs are
    SUPPOSED to differ that way.

    Why not a marker row written by a migration? Because the failure being
    guarded against is a CLONE — and `pg_dump | psql` copies a marker row
    faithfully, so the clone would present the same identity as the original.
    Catalogue identity cannot be copied that way, which is the whole point.
    """
    identity: dict = {"name": None, "oid": None, "cluster": None, "cluster_source": None}
    got, _err = _probe(
        conn,
        "SELECT current_database(), "
        "       (SELECT oid FROM pg_database WHERE datname = current_database())",
    )
    if got:
        identity["name"], identity["oid"] = got[0][0], str(got[0][1])
    got, _err = _probe(conn, "SELECT system_identifier FROM pg_control_system()")
    if got:
        identity["cluster"] = str(got[0][0])
        identity["cluster_source"] = "system_identifier"
    else:
        got, _err = _probe(conn, "SELECT pg_postmaster_start_time()")
        if got:
            identity["cluster"] = str(got[0][0])
            identity["cluster_source"] = "postmaster_start_time"
    return identity


def _judge_database_identity(r: Report) -> None:
    """Both credentials must address the same logical database. No URL leaks."""
    runtime, migration = r.runtime_db, r.migration_db
    problems: list[str] = []

    for label, identity in (("runtime", runtime), ("migration", migration)):
        if not identity or not identity.get("name") or not identity.get("oid"):
            problems.append(
                f"the {label} connection's database identity could not be "
                "established"
            )
    if problems:
        r.database_identity_problems = problems
        r.same_database = False
        return

    if runtime["name"] != migration["name"]:
        problems.append(
            f"runtime is attached to database {runtime['name']!r} and the "
            f"migration credential to {migration['name']!r}"
        )
    if runtime["oid"] != migration["oid"]:
        problems.append(
            "runtime and migration are attached to DIFFERENT databases (same "
            "name, different OID) — a clone with a matching Alembic head would "
            "otherwise pass this check"
        )
    if runtime["cluster"] and migration["cluster"]:
        if runtime["cluster"] != migration["cluster"]:
            problems.append(
                "runtime and migration are on different PostgreSQL clusters "
                f"(compared by {runtime['cluster_source']})"
            )
    else:
        problems.append(
            "no cluster identity could be read from one or both connections, so "
            "two same-named databases on different clusters cannot be told apart"
        )

    r.database_identity_problems = problems
    r.same_database = not problems


def _collect_bare_login_privileges(conn, r: Report, live_tables: set[str]) -> None:
    """What the login holds and can reach with no role assumed. Two methods.

    **Catalogue.** ``aclexplode`` over ``pg_class.relacl``, keeping only grants
    whose grantee is the login itself or ``PUBLIC``. For a NOINHERIT login those
    are exactly the privileges a bare connection carries — anything reached
    through a role needs ``SET ROLE`` first, which is the design. This is also
    the only READ-ONLY way to see INSERT/UPDATE/DELETE: probing a write means
    performing one, and this tool is safe to run against production.

    **Empirical.** A raw ``SELECT`` against every protected table and every
    sequence. The catalogue query can only find grants in the places it looks;
    the probe notices one arriving by a route nobody anticipated, and it is what
    turns "we believe it is revoked" into "we asked, and it said no".

    Only object NAMES are recorded. No probe reads more than one row, and that
    row is discarded — the boolean "did this return anything" is the whole
    result.
    """
    login = r.current_user

    # ── catalogue: direct + PUBLIC grants on tables ───────────────────────
    r.direct_table_grants = sorted(
        f"{name}:{privilege}"
        for name, privilege in _fetch(
            conn,
            "SELECT c.relname, a.privilege_type "
            "  FROM pg_class c "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            "  CROSS JOIN LATERAL aclexplode(c.relacl) a "
            " WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
            "   AND (a.grantee = 0 OR a.grantee = "
            "        (SELECT oid FROM pg_roles WHERE rolname = :login))",
            {"login": login},
        )
    )
    # ── catalogue: direct + PUBLIC grants on sequences ────────────────────
    r.direct_sequence_grants = sorted(
        f"{name}:{privilege}"
        for name, privilege in _fetch(
            conn,
            "SELECT c.relname, a.privilege_type "
            "  FROM pg_class c "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            "  CROSS JOIN LATERAL aclexplode(c.relacl) a "
            " WHERE n.nspname = 'public' AND c.relkind = 'S' "
            "   AND (a.grantee = 0 OR a.grantee = "
            "        (SELECT oid FROM pg_roles WHERE rolname = :login))",
            {"login": login},
        )
    )
    # ── catalogue: default privileges that would grant FUTURE objects ─────
    # A clean database today plus `ALTER DEFAULT PRIVILEGES … GRANT … TO
    # muddato_api` is a database that re-opens itself on the next migration.
    r.default_privilege_grants = sorted(
        f"{objtype}:{privilege}"
        for objtype, privilege in _fetch(
            conn,
            "SELECT d.defaclobjtype, a.privilege_type "
            "  FROM pg_default_acl d "
            "  CROSS JOIN LATERAL aclexplode(d.defaclacl) a "
            " WHERE a.grantee = (SELECT oid FROM pg_roles WHERE rolname = :login)",
            {"login": login},
        )
    )

    # ── empirical: EVERY table, not just the protected subset ─────────────
    #
    # The login holds no privilege on anything, so limiting the sweep to the
    # manifest's protected set would leave `plans`, `platform_settings`,
    # `cron_locks` and `alembic_version` unmeasured — and a stray grant on any
    # of those is the same class of defect as one on `payments`.
    for table in sorted(live_tables):
        r.raw_probed_tables += 1
        rows, err = _probe(conn, f"SELECT 1 FROM {table} LIMIT 1")
        if err is not None:
            r.raw_refused_tables += 1
            continue
        r.raw_permitted_tables.append(table)
        if rows:
            r.raw_leaked_tables.append(table)

    # ── empirical: every sequence ─────────────────────────────────────────
    # `last_value` needs SELECT on the sequence, so this is the read-only probe
    # for exactly the privilege that would let a bare connection mint ids.
    sequences = [
        name for (name,) in _fetch(
            conn,
            "SELECT c.relname FROM pg_class c "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            " WHERE n.nspname = 'public' AND c.relkind = 'S' ORDER BY c.relname",
        )
    ]
    for sequence in sequences:
        r.raw_probed_sequences += 1
        _rows, err = _probe(conn, f"SELECT last_value FROM {sequence}")
        if err is not None:
            r.raw_refused_sequences += 1
        else:
            r.raw_permitted_sequences.append(sequence)



def _collect_capabilities(conn, r: Report) -> None:
    """Owner, ``search_path`` and EXECUTE grants for every declared capability.

    A ``SECURITY DEFINER`` function runs as its owner, so three things decide
    whether it is a capability or a hole: WHO owns it, whether its
    ``search_path`` is pinned (an unpinned one lets a caller who can create
    objects shadow a table name inside the definer's body), and WHO may call it.
    All three are asserted here against the manifest, so adding a function
    without classifying it fails the deploy rather than shipping unreviewed.
    """
    # `oidvectortypes`, not `pg_get_function_identity_arguments`: the latter
    # includes PARAMETER NAMES, so `rls_owning_tenant(p_kind text, p_id uuid)`
    # would never match the manifest's `rls_owning_tenant(text, uuid)` and every
    # function with named arguments would be reported missing.
    # `prorettype = 'pg_catalog.event_trigger'::regtype` — an OID comparison, NOT
    # `format_type(...) = 'event_trigger'`. `format_type` schema-qualifies by
    # SEARCH_PATH VISIBILITY, so a `public.event_trigger` composite type (which
    # needs only CREATE on public — the migration identity has it) renders as the
    # bare string `event_trigger` whenever `public` precedes `pg_catalog`, and a
    # perfectly callable definer returning it would pass a text check. The OID is
    # not shadowable.
    #
    # The EXISTS is the exemption's actual justification, asserted rather than
    # inferred: this function is only unreachable because the DDL machinery is
    # its sole caller, which is true only while it really backs a live, enabled
    # event trigger. `evtenabled <> 'D'` also makes `ALTER EVENT TRIGGER … DISABLE`
    # a deploy failure rather than a silent loss of the control.
    live = {
        f"{name}({args})": (owner, secdef, list(config or []), is_evt, attached)
        for name, args, owner, secdef, config, is_evt, attached in _fetch(
            conn,
            "SELECT p.proname, pg_catalog.oidvectortypes(p.proargtypes), "
            "       pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig, "
            "       p.prorettype = 'pg_catalog.event_trigger'::regtype, "
            "       EXISTS (SELECT 1 FROM pg_event_trigger e "
            "                WHERE e.evtfoid = p.oid AND e.evtenabled <> 'D') "
            "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            " WHERE n.nspname = 'public'",
        )
    }
    runtime_role_names = {*AUTHORITY_ROLES, ROLE_RUNTIME}

    # ── event-trigger definers: classified, and checked on their own terms ──
    #
    # Checked BEFORE the unclassified sweep so a name can only leave that sweep
    # by passing here. The exemption is not "it is on a list"; it is "it is on a
    # list AND the live function really does return event_trigger", so an
    # ordinary definer cannot borrow the name of one and skip every check.
    accepted_event_triggers: set[str] = set()
    for signature in sorted(EVENT_TRIGGER_FUNCTIONS):
        got = live.get(signature)
        if got is None:
            # Provisioned out of band, so a cluster without it is a valid
            # cluster — there is simply no definer here to review, and failing
            # would make the preflight unrunnable anywhere but production.
            #
            # Reported anyway, because absence and "somebody dropped the
            # production control" look identical in a report nobody diffs. It is
            # not a failure because it CANNOT produce an RLS-off table on its
            # own: the schema sweep above reads `relrowsecurity` directly and is
            # the actual control. This trigger only tightens.
            r.event_trigger_functions_absent.append(signature)
            continue
        owner, secdef, config, is_event_trigger, attached = got
        problems: list[str] = []
        if not is_event_trigger:
            problems.append(
                "does not return pg_catalog.event_trigger — it is an ordinary "
                "callable function and must be classified as a capability",
            )
        if not attached:
            problems.append(
                "backs no enabled event trigger — the classification rests on "
                "the DDL machinery being its only caller, which is not true of "
                "a function nothing is attached to",
            )
        # NOT `if secdef and …`: a non-definer event-trigger function runs as
        # whoever issued the DDL, which in production is the schema owner, so an
        # unpinned search_path is a hijack surface during a migration either way.
        if not any(c.startswith("search_path=") for c in config):
            problems.append("search_path is not pinned")
        if owner in runtime_role_names:
            problems.append(
                f"owned by the runtime role {owner!r} — a definer must not run "
                "as an authority a caller can already assume",
            )
        if problems:
            r.event_trigger_problems += [f"{signature}: {p}" for p in problems]
        else:
            accepted_event_triggers.add(signature)
            r.event_trigger_functions_accepted.append(signature)

    # Anything running as its owner that nobody classified. The manifest is an
    # allowlist, and an allowlist that only validates the things already on it
    # is a list, not a control.
    r.unclassified_definer_functions = sorted(
        signature
        for signature, (_owner, secdef, _config, _is_evt, _att) in live.items()
        if secdef
        and signature not in CAPABILITY_FUNCTIONS
        and signature not in accepted_event_triggers
    )

    for signature, grantee in CAPABILITY_FUNCTIONS.items():
        got = live.get(signature)
        if got is None:
            r.capability_problems.append(f"{signature}: missing")
            continue
        owner, secdef, config, _is_evt, _attached = got
        if not secdef:
            r.capability_problems.append(f"{signature}: not SECURITY DEFINER")
        if owner in runtime_role_names:
            r.capability_problems.append(
                f"{signature}: owned by the runtime role {owner!r} — a definer "
                "function must not run as an authority a caller can already assume",
            )
        if not any(c.startswith("search_path=") for c in config):
            r.capability_problems.append(f"{signature}: search_path is not pinned")

        public_exec, grantee_exec = _fetch(
            conn,
            "SELECT has_function_privilege('public', :fn, 'EXECUTE'), "
            "       has_function_privilege(:role, :fn, 'EXECUTE')",
            {"fn": f"public.{signature}", "role": grantee},
        )[0]
        if public_exec:
            r.capability_problems.append(f"{signature}: PUBLIC holds EXECUTE")
        if not grantee_exec:
            r.capability_problems.append(f"{signature}: {grantee} cannot EXECUTE it")
        # A narrow capability must NOT be reachable by every authority. The
        # shared resolvers are granted to the runtime group on purpose, so they
        # are exempt from this one check and only from this one.
        if grantee != ROLE_RUNTIME:
            wide = _fetch(
                conn,
                "SELECT has_function_privilege(:role, :fn, 'EXECUTE')",
                {"fn": f"public.{signature}", "role": ROLE_RUNTIME},
            )[0][0]
            if wide:
                r.capability_problems.append(
                    f"{signature}: {ROLE_RUNTIME} holds EXECUTE — every authority "
                    f"could call a capability meant only for {grantee}",
                )
        if not r.capability_problems or not r.capability_problems[-1].startswith(signature):
            r.capability_functions_ok += 1


def collect_migration(conn, r: Report, *, url: str, runtime_user: str) -> None:
    """The migration identity, checked as the migration identity.

    This is the ONE place a schema-owning connection is acceptable, and it is
    acceptable only because we are deliberately looking at it. The checks are
    the mirror image of the application ones: it must be a DIFFERENT login, it
    must own the schema (otherwise the next release cannot migrate), and it must
    not be the credential the application is configured with.
    """
    r.migration_checked = True
    r.migration_server = _safe_server(url)
    r.migration_user = _fetch(conn, "SELECT current_user")[0][0]
    r.migration_is_separate = r.migration_user != runtime_user
    r.migration_owns_tables = _fetch(
        conn,
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
        "AND pg_get_userbyid(c.relowner) = current_user",
    )[0][0]
    r.migration_db = _database_identity(conn)
    # The runtime login cannot read `alembic_version` — that is the intended
    # posture — so the head is read here, on the identity that owns it.
    try:
        got = _fetch(conn, "SELECT version_num FROM alembic_version")
        r.alembic_head = got[0][0] if len(got) == 1 else None
    except Exception:
        conn.rollback()
        r.alembic_head = None


def check_webhook_routes(r: Report) -> None:
    """No public provider webhook route may carry a router-level worker authority.

    Imported and inspected rather than grepped: a dependency can be attached at
    the router, at the route, or through an included sub-router, and only the
    assembled application knows the answer. This is the check that would have
    caught the defect in the first place — the database cannot see it, because
    from the database's side the request simply arrives as ``muddato_worker``.
    """
    forbidden = {"worker_authority", "platform_authority", "_worker_authority"}
    try:
        from fastapi.routing import APIRoute

        from app.main import app as fastapi_app
    except Exception as exc:
        r.failures.append(f"could not import the application to check routes: {exc!r}")
        return

    for route in fastapi_app.routes:
        if not isinstance(route, APIRoute):
            continue
        if "/webhooks/" not in route.path:
            continue
        r.webhook_routes_checked += 1
        names = {
            getattr(d.call, "__name__", "") for d in route.dependant.dependencies
            if d.call is not None
        }
        if names & forbidden:
            r.webhook_routes_with_authority.append(
                f"{route.path}:{','.join(sorted(names & forbidden))}",
            )


def _judge(r: Report) -> None:
    """Turn the facts into pass/fail. Every entry here is a deploy blocker."""
    # ── the ordinary runtime login ────────────────────────────────────────
    if r.expected_runtime_role and r.current_user != r.expected_runtime_role:
        r.failures.append(
            f"ordinary runtime connects as {r.current_user!r}, expected "
            f"{r.expected_runtime_role!r} — DATABASE_URL is pointed at the wrong role",
        )
    if r.rolsuper:
        r.failures.append("ordinary runtime connects as a SUPERUSER")
    if r.rolbypassrls:
        # No longer a tolerated "managed-platform default". A connection that can
        # bypass row security makes every non-Session code path an unmonitored
        # hole, which is precisely what this correction removes. Provision the
        # restricted login instead of accepting the platform's default one.
        r.failures.append(
            "ordinary runtime connects as a role with BYPASSRLS — row security "
            "would not apply to any connection that skips SET LOCAL ROLE",
        )
    if not r.rolcanlogin:
        r.failures.append("the runtime role cannot LOGIN — it is not a usable credential")
    if r.rolinherit:
        r.failures.append(
            "the runtime login is INHERIT — it would silently hold every authority "
            "role's privileges on a raw connection; it must be NOINHERIT",
        )
    if r.owned_tables:
        r.failures.append(
            f"the runtime login OWNS {len(r.owned_tables)} object(s) — ownership "
            "bypasses row security and permits ALTER/DROP: "
            + ", ".join(r.owned_tables[:8]),
        )
    if r.can_create_in_public:
        r.failures.append("the runtime login can CREATE in schema public")
    if r.can_alter_protected_tables:
        r.failures.append(
            "the runtime login is a member of the table-owning role — it could "
            "ALTER or DROP protected tables",
        )
    if r.unexpected_memberships:
        r.failures.append(
            "the runtime login can assume role(s) it should not: "
            + ", ".join(r.unexpected_memberships),
        )
    if r.missing_memberships:
        r.failures.append(
            "the runtime login cannot assume required authority role(s): "
            + ", ".join(r.missing_memberships),
        )
    if r.raw_leaked_tables:
        r.failures.append(
            "a RAW connection (no SET ROLE, no scope) READ ROWS from: "
            + ", ".join(sorted(r.raw_leaked_tables)[:8])
            + " — the credential itself is not restricted",
        )
    if r.raw_permitted_tables:
        # Separate from the line above on purpose. "Permitted but empty" is not
        # safe: the grant is what matters, and a policy that happens to filter
        # everything today is one edit away from not doing so.
        r.failures.append(
            f"a RAW connection was PERMITTED to query {len(r.raw_permitted_tables)} "
            "protected table(s): "
            + ", ".join(sorted(r.raw_permitted_tables)[:8])
            + " — the login holds a grant it must not have",
        )
    if r.raw_permitted_sequences:
        r.failures.append(
            f"a RAW connection was PERMITTED to read {len(r.raw_permitted_sequences)} "
            "sequence(s): " + ", ".join(sorted(r.raw_permitted_sequences)[:8]),
        )
    if not r.raw_probed_tables:
        r.failures.append(
            "no protected table was probed with the bare login — the sweep did "
            "not run, so nothing here is evidence",
        )
    if r.direct_table_grants:
        r.failures.append(
            "the runtime login (or PUBLIC) holds direct table privilege(s): "
            + ", ".join(r.direct_table_grants[:8]),
        )
    if r.direct_sequence_grants:
        r.failures.append(
            "the runtime login (or PUBLIC) holds direct sequence privilege(s): "
            + ", ".join(r.direct_sequence_grants[:8]),
        )
    if r.default_privilege_grants:
        r.failures.append(
            "ALTER DEFAULT PRIVILEGES would grant future objects to the runtime "
            "login: " + ", ".join(r.default_privilege_grants[:8]),
        )
    if r.unexpected_reachable_roles:
        r.failures.append(
            "the runtime login can reach role(s) it should not, transitively: "
            + ", ".join(r.unexpected_reachable_roles),
        )

    # ── authority roles ───────────────────────────────────────────────────
    for role, (is_super, bypass) in sorted(r.effective_after_downgrade.items()):
        if is_super:
            r.failures.append(f"assuming {role} does not drop SUPERUSER")
        if bypass:
            r.failures.append(
                f"assuming {role} does not drop BYPASSRLS — row security would "
                "not apply to it, whatever the policies say",
            )
    missing_downgrade = [
        role for role in AUTHORITY_ROLES
        if role in r.roles and role not in r.effective_after_downgrade
    ]
    if missing_downgrade:
        r.failures.append(
            "could not verify the privilege downgrade for: "
            + ", ".join(missing_downgrade),
        )
    for name in (*AUTHORITY_ROLES, ROLE_RUNTIME):
        info = r.roles.get(name)
        if info is None:
            r.failures.append(f"authority role {name} does not exist")
            continue
        if info["rolsuper"]:
            r.failures.append(f"{name} is a SUPERUSER")
        if info["rolbypassrls"]:
            r.failures.append(f"{name} has BYPASSRLS")
        if info["rolcanlogin"]:
            r.failures.append(f"{name} can LOGIN — it must only be assumable")
        if not info["rolinherit"]:
            r.failures.append(f"{name} is NOINHERIT — it cannot reach its own grants")
    if r.owned_by_runtime_roles:
        r.failures.append(
            f"{r.owned_by_runtime_roles} protected table(s) are OWNED by an authority "
            "role — ownership bypasses row security",
        )

    # ── policies ──────────────────────────────────────────────────────────
    if r.fail_open_policies:
        r.failures.append(
            f"{len(r.fail_open_policies)} fail-open policy/policies remain: "
            + ", ".join(sorted(r.fail_open_policies)[:8]),
        )
    if r.missing_with_check:
        r.failures.append(
            f"{len(r.missing_with_check)} write policy/policies have no WITH CHECK: "
            + ", ".join(sorted(r.missing_with_check)[:8]),
        )
    if r.missing_policies:
        r.failures.append(
            "protected table(s) with no tenant-authority policy: "
            + ", ".join(r.missing_policies[:8]),
        )
    if r.unclassified_tables:
        r.failures.append(
            "table(s) absent from the RLS manifest: "
            + ", ".join(r.unclassified_tables[:8]),
        )
    if r.leaking_tables:
        r.failures.append(
            "unscoped read returned rows on: " + ", ".join(sorted(r.leaking_tables)[:8]),
        )

    # ── identity posture ──────────────────────────────────────────────────
    if r.identity_users_policies:
        r.failures.append(
            "the pre-authentication identity authority has a policy on 'users': "
            + ", ".join(sorted(r.identity_users_policies))
            + " — it must have none, so its direct directory access is deny-all",
        )
    if r.identity_users_rows:
        r.failures.append(
            "the pre-authentication identity authority READ the users directory",
        )

    # ── capability functions ──────────────────────────────────────────────
    for problem in r.capability_problems:
        r.failures.append(f"capability function {problem}")
    for problem in r.event_trigger_problems:
        r.failures.append(f"event-trigger function {problem}")
    if r.unclassified_definer_functions:
        r.failures.append(
            "SECURITY DEFINER function(s) absent from the capability manifest: "
            + ", ".join(r.unclassified_definer_functions[:8])
            + " — a definer function runs as its owner, so an unreviewed one is "
            "a privilege-escalation primitive nobody has looked at",
        )

    # ── webhook posture ───────────────────────────────────────────────────
    if r.webhook_routes_with_authority:
        r.failures.append(
            "public webhook route(s) carry a router-level cross-tenant authority: "
            + ", ".join(sorted(r.webhook_routes_with_authority)[:8]),
        )

    # ── migration posture ─────────────────────────────────────────────────
    if not r.migration_checked:
        r.failures.append(
            "the migration credential was not checked — MIGRATION_DATABASE_URL_SYNC "
            "must be set so the two identities can be verified as distinct",
        )
    else:
        if not r.migration_is_separate:
            r.failures.append(
                "the migration credential and the runtime credential are the SAME "
                f"login ({r.migration_user!r}) — ordinary traffic must not run as "
                "the schema owner",
            )
        if not r.migration_owns_tables:
            r.failures.append(
                "the migration credential owns no table in schema public — it "
                "cannot run the next release's DDL",
            )
        _judge_database_identity(r)
        for problem in r.database_identity_problems:
            r.failures.append(f"database identity: {problem}")

    # ── migration head ────────────────────────────────────────────────────
    if r.expected_head and r.alembic_head != r.expected_head:
        r.failures.append(
            f"migration head is {r.alembic_head!r}, expected {r.expected_head!r}",
        )
    if r.expected_head is None:
        r.failures.append("migration chain does not have exactly one head")


def render(r: Report) -> str:
    lines = [
        "PREFLIGHT — APPLICATION POSTURE",
        f"  database                 {r.database}",
        f"  server                   {r.server}",
        f"  current_user             {r.current_user} (expected {r.expected_runtime_role})",
        f"  session_user             {r.session_user}",
        f"  LOGIN                    {r.rolcanlogin}",
        f"  SUPERUSER                {r.rolsuper}",
        f"  BYPASSRLS                {r.rolbypassrls}",
        f"  INHERIT                  {r.rolinherit}  (must be False)",
        f"  owns protected tables    {r.owns_protected_tables}",
        f"  CREATE on public         {r.can_create_in_public}",
        f"  member of table owner    {r.can_alter_protected_tables}",
        f"  assumable authorities    {', '.join(r.login_memberships) or '(none)'}",
        f"  bare-login tables        {r.raw_refused_tables}/{r.raw_probed_tables} refused"
        f"  (permitted={len(r.raw_permitted_tables)}, leaked={len(r.raw_leaked_tables)};"
        f" {r.protected_tables} of them protected)",
        f"  bare-login sequences     {r.raw_refused_sequences}/{r.raw_probed_sequences} refused"
        + ("  (none in this schema — every key is a UUID, so this number is not "
           "evidence of anything until one exists)"
           if r.raw_probed_sequences == 0
           else f"  (permitted={len(r.raw_permitted_sequences)})"),
        f"  direct table grants      {len(r.direct_table_grants)}  (must be 0)",
        f"  direct sequence grants   {len(r.direct_sequence_grants)}  (must be 0)",
        f"  default-privilege grants {len(r.default_privilege_grants)}  (must be 0)",
        f"  transitively reachable   {', '.join(r.reachable_roles) or '(none)'}",
        "",
        "PREFLIGHT — MIGRATION POSTURE",
        f"  checked                  {r.migration_checked}",
        f"  server                   {r.migration_server}",
        f"  current_user             {r.migration_user}",
        f"  separate from runtime    {r.migration_is_separate}",
        f"  owns tables              {r.migration_owns_tables}",
        f"  same logical database    {r.same_database}",
        f"    runtime  db/oid        {r.runtime_db.get('name')}/{r.runtime_db.get('oid')}",
        f"    migration db/oid       {r.migration_db.get('name')}/{r.migration_db.get('oid')}",
        f"    cluster match          "
        f"{r.runtime_db.get('cluster') == r.migration_db.get('cluster')}"
        f"  (by {r.runtime_db.get('cluster_source')})",
        f"  migration head           {r.alembic_head} (expected {r.expected_head})",
        "",
        "PREFLIGHT — SCHEMA POSTURE",
        f"  protected tables         {r.protected_tables}",
        f"  RLS-enabled tables       {r.rls_enabled_tables}",
        f"  FORCE-RLS tables         {r.force_rls_tables}  (0 by design — see rf0failclosed)",
        f"  tables owned by runtime  {r.owned_by_runtime_roles}",
        f"  classified policies      {r.classified_policies}",
        f"  fail-open policies       {len(r.fail_open_policies)}",
        f"  missing WITH CHECK       {len(r.missing_with_check)}",
        f"  unclassified tables      {len(r.unclassified_tables)}",
        f"  unscoped read leaks      {len(r.leaking_tables)}",
        f"  probes refused outright  {r.refused_probes}",
        "",
        "PREFLIGHT — IDENTITY POSTURE",
        f"  policies on users        {len(r.identity_users_policies)}  (must be 0)",
        f"  directory rows read      {r.identity_users_rows}  (must be 0/None)",
        f"  capability functions ok  {r.capability_functions_ok}/{len(CAPABILITY_FUNCTIONS)}",
        f"  event-trigger definers   {len(r.event_trigger_functions_accepted)} accepted"
        + (f", {len(r.event_trigger_problems)} rejected" if r.event_trigger_problems else "")
        + (f"  ({', '.join(r.event_trigger_functions_accepted)})"
           if r.event_trigger_functions_accepted else ""),
        *([f"  event-trigger absent     {', '.join(r.event_trigger_functions_absent)}"
           "  (not a failure — provisioned out of band; the schema sweep above is "
           "the control)"] if r.event_trigger_functions_absent else []),
        f"  unclassified definers    {len(r.unclassified_definer_functions)}  (must be 0)",
        "",
        "PREFLIGHT — WEBHOOK POSTURE",
        f"  public routes checked    {r.webhook_routes_checked}",
        f"  with router authority    {len(r.webhook_routes_with_authority)}  (must be 0)",
        "",
    ]
    for name in AUTHORITY_ROLES:
        info = r.roles.get(name)
        lines.append(
            f"  role {name:<18} "
            + ("ABSENT" if info is None else
               f"super={info['rolsuper']} bypassrls={info['rolbypassrls']} "
               f"login={info['rolcanlogin']} inherit={info['rolinherit']}")
        )
    for role, (is_super, bypass) in sorted(r.effective_after_downgrade.items()):
        lines.append(
            f"  after SET ROLE {role:<18} super={is_super} bypassrls={bypass}"
        )
    if r.risks:
        lines.append("")
        lines.append("RISKS (recorded, not blocking):")
        lines += [f"  - {x}" for x in r.risks]
    lines.append("")
    if r.failures:
        lines.append(f"FAIL ({len(r.failures)}):")
        lines += [f"  - {f}" for f in r.failures]
    else:
        lines.append("PASS")
    return "\n".join(lines)


def inventory(conn) -> str:
    """The full per-table RLS inventory, as a markdown table.

    Generated rather than transcribed: a hand-written inventory is out of date
    the day after it is written, and the interesting columns (owner, FORCE flag,
    the actual USING and WITH CHECK expressions) are exactly the ones nobody
    updates by hand.

    Read-only. Reads catalogues only — never a data row — so it prints no PII.
    """
    rows = _fetch(
        conn,
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "       pg_get_userbyid(c.relowner), "
        "       (SELECT bool_or(a.attname = 'tenant_id') FROM pg_attribute a "
        "         WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped), "
        "       COALESCE((SELECT NOT a.attnotnull FROM pg_attribute a "
        "         WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'), false) "
        "  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        " WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname",
    )
    policies: dict[str, list[tuple]] = {}
    for table, name, cmd, roles, qual, check in _fetch(
        conn,
        "SELECT tablename, policyname, cmd, roles::text, "
        "       COALESCE(qual, '-'), COALESCE(with_check, '-') "
        "  FROM pg_policies WHERE schemaname = 'public' "
        " ORDER BY tablename, policyname",
    ):
        policies.setdefault(table, []).append((name, cmd, roles, qual, check))

    def _squash(expr: str) -> str:
        return " ".join(expr.split()).replace("|", "\\|")

    out = [
        "| table | tenant_id | nullable | RLS | FORCE | owner | category |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, rls, force, owner, has_tid, nullable in rows:
        cat = CLASSIFICATION.get(name)
        out.append(
            f"| `{name}` | {'yes' if has_tid else 'no'} | "
            f"{'yes' if nullable else 'no'} | {'on' if rls else 'OFF'} | "
            f"{'on' if force else 'off'} | `{owner}` | "
            f"{cat.value if cat else '**UNCLASSIFIED**'} |"
        )

    out += ["", "### Expected access path, by category", "",
            "| category | tenant | customer | platform | worker |", "|---|---|---|---|---|"]
    for cat, paths in EXPECTED_PATHS.items():
        out.append(
            f"| {cat.value} | {paths['tenant']} | {paths['customer']} | "
            f"{paths['platform']} | {paths['worker']} |"
        )

    out += ["", "### Policies", "",
            "| table | policy | cmd | roles | USING | WITH CHECK |",
            "|---|---|---|---|---|---|"]
    for table in sorted(policies):
        for pname, cmd, roles, qual, check in policies[table]:
            out.append(
                f"| `{table}` | `{pname}` | {cmd} | `{roles}` | "
                f"`{_squash(qual)}` | `{_squash(check)}` |"
            )

    out += ["", "### Capability functions", "",
            "| function | owner | definer | search_path | EXECUTE |",
            "|---|---|---|---|---|"]
    for name, args, owner, secdef, config in _fetch(
        conn,
        "SELECT p.proname, pg_catalog.oidvectortypes(p.proargtypes), "
        "       pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig "
        "  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        " WHERE n.nspname = 'public' ORDER BY p.proname",
    ):
        sig = f"{name}({args})"
        pinned = next(
            (c for c in (config or []) if c.startswith("search_path=")), "**UNPINNED**",
        )
        out.append(
            f"| `{sig}` | `{owner}` | {'yes' if secdef else 'no'} | `{pinned}` | "
            f"`{CAPABILITY_FUNCTIONS.get(sig, '(unclassified)')}` |"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--url", default=None, help="override the runtime sync DB URL")
    parser.add_argument(
        "--migration-url", default=None, help="override the migration sync DB URL",
    )
    parser.add_argument(
        "--inventory", action="store_true",
        help="print the full per-table RLS inventory as markdown and exit 0",
    )
    parser.add_argument(
        "--skip-routes", action="store_true",
        help="skip the in-process webhook route check (for DB-only environments)",
    )
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine

    from app.config import settings

    url = args.url or settings.database_url_sync
    engine = create_engine(url, poolclass=None)
    try:
        with engine.connect() as conn:
            if args.inventory:
                print(inventory(conn))
                conn.rollback()
                return 0
            report = collect(
                conn, url=url, expected_runtime_role=settings.db_runtime_role,
            )
            conn.rollback()  # nothing above writes; make that explicit
    finally:
        engine.dispose()

    # The migration identity, on its own connection. Resolved through settings so
    # a missing MIGRATION_DATABASE_URL is a clear operator error in production
    # rather than a silent fallback to the runtime credential.
    migration_url = args.migration_url
    if migration_url is None:
        try:
            migration_url = settings.migration_url_sync
        except RuntimeError as exc:
            report.failures.append(str(exc))
            migration_url = None
    if migration_url is not None:
        mig_engine = create_engine(migration_url, poolclass=None)
        try:
            with mig_engine.connect() as conn:
                collect_migration(
                    conn, report, url=migration_url, runtime_user=report.current_user,
                )
                conn.rollback()
        except Exception as exc:
            report.failures.append(
                f"could not connect with the migration credential: {type(exc).__name__}",
            )
        finally:
            mig_engine.dispose()

    if not args.skip_routes:
        check_webhook_routes(report)

    # One judgement, once, over the application + migration + route facts.
    _judge(report)

    print(json.dumps(report.as_dict(), indent=2, default=str) if args.json
          else render(report))
    return 1 if report.failures else 0


if __name__ == "__main__":  # pragma: no cover — module entry point
    sys.exit(main())
