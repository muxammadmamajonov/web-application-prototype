"""Create, repair and verify the restricted runtime login — the cutover bootstrap.

    # create / repair (as the schema owner)
    export MUDDATO_BOOTSTRAP_MIGRATION_URL='postgresql+psycopg://owner:…@host/db'
    export RUNTIME_DB_PASSWORD_FD=3          # preferred; see "the password" below
    uv run python -m scripts.bootstrap_runtime_login 3< /run/secrets/runtime_pw

    # prove the result independently, connecting AS the new login
    export MUDDATO_BOOTSTRAP_VERIFY_URL='postgresql+psycopg://muddato_api:…@host/db'
    uv run python -m scripts.bootstrap_runtime_login --verify

WHY THIS TOOL EXISTS — the deadlock it breaks
---------------------------------------------
The final posture requires two different database logins, and
``app/config.py`` enforces that: in production, ``Settings`` refuses to
construct when the runtime and migration URLs name the same role. That check is
correct and must not be relaxed.

But it runs at **module import** of ``app.config``, and ``alembic/env.py``
imports ``app.config``. So from the real pre-cutover posture — schema at
``rf0failclosed``, ``DATABASE_URL`` and the migration URL both the owner,
``muddato_api`` not yet existing — the documented sequence was impossible:

    set MIGRATION_DATABASE_URL_SYNC → both URLs name the owner
      → importing app.config raises
        → `alembic upgrade head` cannot start
          → rg0credsplit never runs
            → muddato_api is never created
              → DATABASE_URL can never be repointed

A final-posture invariant was being enforced by a component the bootstrap path
itself has to import. The invariant is right; the coupling was the bug.

This tool breaks the loop by not participating in it. **It never imports
``app.config``.** Its only inputs are environment variables it reads directly,
so it runs happily against a cluster whose deployed configuration is still the
old single-credential one.

WHAT IT WILL NOT DO
-------------------
* It will not read or accept the application ``Settings``, so it cannot be
  blocked by — or quietly satisfy — a posture check meant for the running app.
* It will not take a URL or a password on the command line. Both arrive out of
  band, because ``argv`` is visible in ``ps`` output, in shell history and in
  most process supervisors' logs.

  **There is no ``psql`` companion, deliberately.** A
  ``bootstrap_runtime_login.sql`` did exist, and it told the operator to pass
  the password as a psql variable — which is a process argument, i.e. exactly
  the leak this paragraph forbids. It was deleted rather than patched: a second
  bootstrap path that has to be kept as careful as this one is a second path to
  get wrong.
* It will not print, log, echo or transmit the password. PostgreSQL cannot bind
  a password as a query parameter — ``ALTER ROLE … PASSWORD`` is a utility
  statement — so instead of escaping the plaintext into SQL, the tool computes
  the **SCRAM-SHA-256 verifier locally** and sends that. The password itself
  never crosses the connection, never reaches the server log or
  ``pg_stat_activity``, and never appears in this tool's output, which reports
  only whether a password is now set.
* It will not grant the login any table privilege, ``CREATE``, or ownership. It
  narrows on every run, including runs where the role already existed.

IT IS SAFE BEFORE ``rg0credsplit``
-----------------------------------
At ``rf0failclosed`` the ``muddato_webhook`` role does not exist yet. The tool
grants the authority roles it FINDS and reports the rest as pending; the
migration grants the remainder when it runs. Nothing here depends on rg0 having
been applied, and re-running after rg0 converges on the full set.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
from typing import Any

from sqlalchemy import create_engine, text

# `app.core.rls_manifest` is pure data — it imports `enum` and nothing else, and
# in particular NOT `app.config`. Asserted by
# tests/test_credential_cutover_rehearsal.py so this stays true.
from app.core.rls_manifest import AUTHORITY_ROLES, ROLE_API_LOGIN

#: The owner/migration URL. An environment variable, never `argv`.
URL_ENV = "MUDDATO_BOOTSTRAP_MIGRATION_URL"
#: The restricted login's own URL, for `--verify`.
VERIFY_URL_ENV = "MUDDATO_BOOTSTRAP_VERIFY_URL"
#: Where the password comes from, most preferred first. All three are named by
#: the environment; none is a command-line argument.
#:
#: ``RUNTIME_DB_PASSWORD_FD``    an inherited file descriptor, e.g.
#:                               ``… 3< /run/secrets/pw`` with ``…_FD=3``. The
#:                               secret never exists as a process attribute at
#:                               all — not in ``argv``, not in ``environ``.
#: ``RUNTIME_DB_PASSWORD_FILE``  a path to read. The path is not the secret.
#: ``RUNTIME_DB_PASSWORD``       the value itself. Accepted because it is what a
#:                               CI runner and most secret managers hand you,
#:                               and it is strictly better than ``argv``:
#:                               ``/proc/<pid>/environ`` is readable only by the
#:                               process owner, while ``argv`` is world-readable
#:                               through ``ps``.
PASSWORD_FD_ENV = "RUNTIME_DB_PASSWORD_FD"
PASSWORD_FILE_ENV = "RUNTIME_DB_PASSWORD_FILE"
PASSWORD_ENV = "RUNTIME_DB_PASSWORD"


def _read_password() -> str | None:
    """The password, from the safest source that is configured.

    Every failure message here names the SOURCE and never the value — a tool
    that echoes a secret while complaining about it is worse than one that never
    reads it.
    """
    raw_fd = os.environ.get(PASSWORD_FD_ENV)
    if raw_fd:
        try:
            fd = int(raw_fd)
        except ValueError:
            raise SystemExit(_fail(f"{PASSWORD_FD_ENV} is not an integer")) from None
        try:
            with os.fdopen(fd, "r", closefd=True) as handle:
                return handle.read().strip() or None
        except OSError as exc:
            raise SystemExit(_fail(
                f"could not read the password from {PASSWORD_FD_ENV}={fd} "
                f"({type(exc).__name__}) — was the descriptor inherited?"
            )) from None

    path = os.environ.get(PASSWORD_FILE_ENV)
    if path:
        try:
            return pathlib.Path(path).read_text().strip() or None
        except OSError as exc:
            raise SystemExit(_fail(
                f"could not read the password file named by {PASSWORD_FILE_ENV} "
                f"({type(exc).__name__})"
            )) from None

    return os.environ.get(PASSWORD_ENV) or None


def _fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 2


def _statements(existing_roles: set[str]) -> tuple[str, ...]:
    """Everything except the password, all idempotent, all narrowing.

    Ordered so a partially-applied previous run converges rather than erroring:
    create, then re-assert attributes, then narrow privileges, then grant
    memberships.
    """
    return (
        "SET lock_timeout = '5s'",
        # Roles are cluster-scoped → guard creation with a catalogue check.
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE_API_LOGIN}') THEN "
        f"CREATE ROLE {ROLE_API_LOGIN} LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS "
        "NOCREATEDB NOCREATEROLE; "
        "END IF; END $$",
        # NOINHERIT is re-asserted on EVERY run, not just at creation. It is the
        # single property that keeps a raw connection powerless, and a
        # well-meaning `ALTER ROLE … INHERIT` would hand every direct connection
        # muddato_worker's cross-tenant reach without changing anything visible.
        f"ALTER ROLE {ROLE_API_LOGIN} LOGIN NOINHERIT NOCREATEDB NOCREATEROLE",
        # USAGE lets the login RESOLVE a name; it grants access to nothing. Without
        # it every refusal reads as "relation does not exist", which looks like a
        # schema bug rather than an authorization decision.
        f"GRANT USAGE ON SCHEMA public TO {ROLE_API_LOGIN}",
        f"REVOKE CREATE ON SCHEMA public FROM {ROLE_API_LOGIN}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE_API_LOGIN}",
        f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE_API_LOGIN}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE ALL ON TABLES FROM {ROLE_API_LOGIN}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE ALL ON SEQUENCES FROM {ROLE_API_LOGIN}",
        # Only the authority roles that EXIST. Before rg0credsplit,
        # `muddato_webhook` does not, and granting a role that is not there is a
        # hard error that would abort an otherwise-correct bootstrap.
        *(
            f"GRANT {role} TO {ROLE_API_LOGIN}"
            for role in AUTHORITY_ROLES if role in existing_roles
        ),
    )


def _clear_dangerous_attributes(conn: Any) -> None:
    """Attempt ``NOSUPERUSER``/``NOBYPASSRLS``; warn rather than wedge.

    Only a superuser can clear these, and the migration identity deliberately is
    not one. A bootstrap that died here would be unusable on the platform we run
    on; instead the posture check below reports it, non-zero, so the operator
    learns before the deploy rather than during it.
    """
    conn.execute(text(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{ROLE_API_LOGIN}' "
        "AND (rolsuper OR rolbypassrls)) THEN BEGIN "
        f"EXECUTE 'ALTER ROLE {ROLE_API_LOGIN} NOSUPERUSER NOBYPASSRLS'; "
        "EXCEPTION WHEN insufficient_privilege THEN "
        f"RAISE WARNING 'cannot clear SUPERUSER/BYPASSRLS on {ROLE_API_LOGIN} "
        "(%); run this as a superuser before cutting over', SQLERRM; "
        "END; END IF; END $$;"
    ))


def _password_is_set(conn: Any) -> bool | None:
    """Whether the role has a password — or None when we are not allowed to look.

    ``pg_authid`` is superuser-only, and the migration identity deliberately is
    not a superuser (Supabase's owner is not). The public ``pg_roles`` view
    masks ``rolpassword`` to a constant, so it cannot answer the question
    either. Rather than demand a privilege the bootstrap must not need, this
    returns None and the real proof moves to ``--verify``, which settles it the
    only way that actually matters: by authenticating.
    """
    try:
        got = conn.execute(text(
            "SELECT rolpassword IS NOT NULL FROM pg_authid WHERE rolname = :role"
        ), {"role": ROLE_API_LOGIN}).scalar()
        return bool(got)
    except Exception:
        return None


def _posture(conn: Any) -> dict[str, Any]:
    """Read back what the role actually is. Facts only — no credential."""
    row = conn.execute(text(
        "SELECT rolcanlogin, rolinherit, rolsuper, rolbypassrls "
        "  FROM pg_roles WHERE rolname = :role"
    ), {"role": ROLE_API_LOGIN}).first()
    if row is None:
        return {}
    held = {
        name for (name,) in conn.execute(text(
            "SELECT g.rolname FROM pg_auth_members m "
            "JOIN pg_roles g ON g.oid = m.roleid "
            "JOIN pg_roles u ON u.oid = m.member "
            "WHERE u.rolname = :role"
        ), {"role": ROLE_API_LOGIN}).all()
    }
    owned = conn.execute(text(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        " WHERE n.nspname='public' AND c.relkind='r' "
        "   AND pg_get_userbyid(c.relowner) = :role"
    ), {"role": ROLE_API_LOGIN}).scalar()
    can_create = conn.execute(text(
        "SELECT has_schema_privilege(:role, 'public', 'CREATE')"
    ), {"role": ROLE_API_LOGIN}).scalar()
    return {
        "login": row[0], "inherit": row[1], "super": row[2],
        "bypassrls": row[3], "password_set": _password_is_set(conn),
        "memberships": sorted(held), "owns_tables": owned,
        "can_create": can_create,
    }


def _judge(
    posture: dict[str, Any], *, existing_roles: set[str], password_supplied: bool,
) -> list[str]:
    """Everything wrong with the resulting role. Empty list == exit 0."""
    if not posture:
        return [f"{ROLE_API_LOGIN} does not exist"]
    problems: list[str] = []
    if not posture["login"]:
        problems.append("cannot LOGIN")
    if posture["inherit"]:
        problems.append("is INHERIT — it must be NOINHERIT")
    if posture["super"]:
        problems.append("is SUPERUSER — clear it as a superuser")
    if posture["bypassrls"]:
        problems.append("has BYPASSRLS — clear it as a superuser")
    if posture["owns_tables"]:
        problems.append(f"owns {posture['owns_tables']} protected table(s)")
    if posture["can_create"]:
        problems.append("can CREATE in schema public")
    # None means "not allowed to look" (pg_authid is superuser-only), which is
    # the normal case for the migration identity. Only a definite False is a
    # problem, and only when this run did not just set one.
    if posture["password_set"] is False and not password_supplied:
        problems.append(
            f"has no password — set {PASSWORD_ENV} and re-run, or the "
            "application cannot authenticate"
        )
    # Only roles that exist can have been granted; anything else is pending on
    # the migration, which is a normal pre-rg0 state and not a failure.
    wanted = {r for r in AUTHORITY_ROLES if r in existing_roles}
    missing = wanted - set(posture["memberships"])
    if missing:
        problems.append(f"cannot assume existing authority role(s): {sorted(missing)}")
    unexpected = set(posture["memberships"]) - set(AUTHORITY_ROLES)
    if unexpected:
        problems.append(f"can assume role(s) it should not: {sorted(unexpected)}")
    return problems


def scram_verifier(password: str, *, salt: bytes | None = None,
                   iterations: int = 4096) -> str:
    """The SCRAM-SHA-256 verifier for ``password``, computed HERE.

    PostgreSQL has no way to bind a password as a query parameter —
    ``ALTER ROLE … PASSWORD`` is a utility statement, and a ``DO`` block's body
    is an opaque string literal that parameters cannot reach into. Every tool
    that sets a password therefore puts it in the SQL text, where it lands in
    the server log, in ``pg_stat_activity``, and in anything watching the wire.

    So the plaintext is never sent. PostgreSQL accepts a **pre-computed
    verifier** in place of a password, and that is what this builds: the
    operator's password is hashed on the operator's machine, and what crosses
    the connection is a value that cannot be replayed as a password anywhere
    else. The statement below therefore contains no secret to leak.

    Format (``src/common/scram-common.c``)::

        SCRAM-SHA-256$<iterations>:<b64 salt>$<b64 StoredKey>:<b64 ServerKey>

    ASCII only, deliberately. Correct handling of a non-ASCII password needs
    SASLprep (RFC 4013), not merely NFKC, and quietly applying the wrong
    normalisation would produce a verifier that fails to authenticate for
    reasons nobody could see. A database credential can be ASCII; a silent
    mismatch at cutover cannot be debugged from the error message.
    """
    import base64
    import hashlib
    import hmac
    import secrets

    if not password.isascii():
        raise ValueError(
            "the runtime password must be ASCII: a non-ASCII password needs "
            "SASLprep normalisation, and getting that subtly wrong yields a "
            "verifier that silently fails to authenticate"
        )
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    def b64(raw: bytes) -> str:
        """PostgreSQL stores each verifier component base64-encoded."""
        return base64.b64encode(raw).decode()

    return (
        f"SCRAM-SHA-256${iterations}:{b64(salt)}"
        f"${b64(stored_key)}:{b64(server_key)}"
    )


def _render_password_state(posture: dict[str, Any], supplied: bool) -> str:
    """Report the password STATE, never the password."""
    known = posture.get("password_set")
    if known is True:
        return "yes"
    if known is False:
        return "set-this-run" if supplied else "NO"
    return "set-this-run" if supplied else "unknown (pg_authid needs superuser)"


def bootstrap(url: str, password: str | None) -> int:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            who = conn.execute(text("SELECT current_user")).scalar()
            existing = {
                name for (name,) in conn.execute(text(
                    "SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"
                ), {"names": list(AUTHORITY_ROLES)}).all()
            }
            for stmt in _statements(existing):
                conn.execute(text(stmt))
            _clear_dangerous_attributes(conn)
            if password:
                # The plaintext never leaves this process. What goes on the wire
                # is a SCRAM-SHA-256 verifier computed above — so the password is
                # absent from the SQL text, from the server log, from
                # pg_stat_activity, and from anything watching the connection.
                #
                # The verifier is a fixed-shape ASCII token this module built, so
                # embedding it in the statement is not an injection surface; it
                # contains no quote and no character a literal could escape.
                #
                # `exec_driver_sql`, not `text()`: a SCRAM verifier contains
                # `:` (between iteration count and salt), and `text()` reads
                # `:name` as a bind parameter — it turned the verifier into a
                # placeholder and failed asking for a value. Driver-level
                # execution passes the statement through untouched, and with no
                # parameters psycopg does not interpret `%` either.
                verifier = scram_verifier(password)
                try:
                    conn.exec_driver_sql(
                        f"ALTER ROLE {ROLE_API_LOGIN} PASSWORD '{verifier}'"
                    )
                except Exception as exc:
                    # Re-raised WITHOUT the original message. A driver exception
                    # quotes the failing statement, and that statement carries
                    # the verifier — which is not the password, but is still a
                    # credential-equivalent for this role and has no business in
                    # a traceback, a CI log or a bug report.
                    raise RuntimeError(
                        "failed to set the password on "
                        f"{ROLE_API_LOGIN} ({type(exc).__name__}); the statement "
                        "is withheld because it contains the SCRAM verifier"
                    ) from None
            posture = _posture(conn)
    finally:
        engine.dispose()

    pending = [r for r in AUTHORITY_ROLES if r not in existing]
    print(f"connected as              {who}")
    print(f"{ROLE_API_LOGIN:<25} login={posture.get('login')} "
          f"inherit={posture.get('inherit')} super={posture.get('super')} "
          f"bypassrls={posture.get('bypassrls')} "
          f"password_set={_render_password_state(posture, bool(password))}")
    print(f"owns protected tables     {posture.get('owns_tables')}")
    print(f"CREATE on public          {posture.get('can_create')}")
    print(f"assumable authorities     {', '.join(posture.get('memberships', [])) or '(none)'}")
    if pending:
        print(f"pending until rg0credsplit {', '.join(pending)}")

    problems = _judge(
        posture, existing_roles=existing, password_supplied=bool(password),
    )
    for p in problems:
        print(f"  ! {ROLE_API_LOGIN} {p}", file=sys.stderr)
    if problems:
        return 1
    print("\nOK. Next: --verify through the restricted login, then stage the "
          "runtime secrets and deploy. See docs/RUNBOOK.md §8.3.")
    return 0


def verify(url: str) -> int:
    """Connect AS the restricted login and prove the posture from the inside.

    A separate step on purpose. The bootstrap above proves what the catalogue
    says; this proves the credential actually authenticates and actually cannot
    reach anything — which is the property the deploy depends on, and the one a
    typo in the URL or a missing pooler route would break.
    """
    engine = create_engine(url)
    problems: list[str] = []

    def _probe(statement: str, setup: str | None = None) -> str | None:
        """Run one statement on its OWN connection; return the failure kind.

        A fresh connection per probe, deliberately. A refusal aborts the
        surrounding transaction, and every later probe on the same connection
        would then fail with "current transaction is aborted" — reporting the
        wrong reason for the right answer, or the right answer for no reason.
        """
        with engine.connect() as probe_conn:
            try:
                if setup:
                    probe_conn.execute(text(setup))
                probe_conn.execute(text(statement))
                return None
            except Exception as exc:
                return type(exc).__name__
            finally:
                probe_conn.rollback()

    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT current_user, rolcanlogin, rolinherit, rolsuper, rolbypassrls "
                "  FROM pg_roles WHERE rolname = current_user"
            )).first()
            who, login, inherit, is_super, bypass = row
            print(f"authenticated as          {who}")
            print(f"login={login} inherit={inherit} super={is_super} bypassrls={bypass}")
            if who != ROLE_API_LOGIN:
                problems.append(f"authenticated as {who!r}, expected {ROLE_API_LOGIN!r}")
            if inherit:
                problems.append("is INHERIT")
            if is_super:
                problems.append("is SUPERUSER")
            if bypass:
                problems.append("has BYPASSRLS")
    except Exception as exc:
        engine.dispose()
        return _fail(f"could not connect as the restricted login: {type(exc).__name__}")

    try:
        # The property the whole design rests on: a raw connection reaches nothing.
        refused = _probe("SELECT 1 FROM users LIMIT 1")
        if refused is None:
            problems.append(
                "a RAW connection was PERMITTED to read `users` — the login "
                "holds a grant it must not have"
            )
            print("raw read of users         PERMITTED  (must be refused)")
        else:
            print(f"raw read of users         refused ({refused})")

        # And it can still become what it needs to be.
        for role in AUTHORITY_ROLES:
            failed = _probe("SELECT 1", setup=f"SET LOCAL ROLE {role}")
            if failed is None:
                print(f"can assume                {role}")
            else:
                print(f"CANNOT assume             {role} ({failed})")
                problems.append(f"cannot assume {role}")
    finally:
        engine.dispose()

    for p in problems:
        print(f"  ! {p}", file=sys.stderr)
    if problems:
        return 1
    print("\nVERIFIED. The restricted login authenticates, reaches nothing "
          "directly, and can assume every authority it needs.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        epilog=(
            f"URLs are read from {URL_ENV} / {VERIFY_URL_ENV}; the password from "
            f"{PASSWORD_FD_ENV} (preferred), {PASSWORD_FILE_ENV}, or "
            f"{PASSWORD_ENV}. Never from the command line, because argv is "
            "visible in ps output and shell history."
        ),
    )
    parser.add_argument(
        "--verify", action="store_true",
        help=f"connect as the restricted login ({VERIFY_URL_ENV}) and check it",
    )
    args = parser.parse_args(argv)

    if args.verify:
        url = os.environ.get(VERIFY_URL_ENV)
        if not url:
            return _fail(
                f"{VERIFY_URL_ENV} is not set. Export the RESTRICTED login's URL "
                "(never pass it as an argument) and re-run with --verify."
            )
        return verify(url)

    url = os.environ.get(URL_ENV)
    if not url:
        return _fail(
            f"{URL_ENV} is not set. Export the SCHEMA OWNER's URL (never pass it "
            "as an argument) and re-run.\n"
            "  This tool deliberately does not read the application settings: it "
            "has to work from the pre-cutover posture, where importing them "
            "fails because the runtime and migration URLs are still the same "
            "role."
        )
    return bootstrap(url, _read_password())


if __name__ == "__main__":  # pragma: no cover — operator entry point
    sys.exit(main())
