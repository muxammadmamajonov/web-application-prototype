"""Database engine, session factory, and declarative base."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import MetaData, event, text
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import settings

# Predictable naming convention so Alembic autogenerates stable names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(AsyncAttrs, DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _is_transaction_pooler(url: str) -> bool:
    """Supabase transaction pooler (port 6543) runs in transaction mode, which
    forbids prepared statements + session-level GUCs."""
    return "+asyncpg" in url and ("6543" in url or "pooler.supabase.com" in url)


def _engine_kwargs(url: str) -> dict[str, object]:
    """Engine kwargs derived from settings — the connection-budget + timeout
    knobs live here so they're env-driven and unit-testable (#68)."""
    connect_args: dict[str, object] = {
        # asyncpg client-side per-query timeout (seconds). Pooler-safe and the
        # primary guard: it cancels a query stuck on a slow scan OR a lock wait,
        # freeing the connection so it can't pile up.
        "command_timeout": settings.db_statement_timeout_ms / 1000,
    }
    if _is_transaction_pooler(url):
        # Disable asyncpg's caches + give every statement a unique name so the
        # pooler never sees a prepared-statement collision.
        connect_args.update({
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4().hex}__",
        })
    else:
        # Direct/session connection: also enforce server-side ceilings (the
        # transaction pooler forbids these session params; on the pooler set
        # them at the Supabase role level instead — see docs/RUNBOOK.md).
        connect_args["server_settings"] = {
            "statement_timeout": str(settings.db_statement_timeout_ms),
            "lock_timeout": str(settings.db_lock_timeout_ms),
        }
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,
        "pool_recycle": 1800,   # recycle every 30 min so the pooler doesn't hand back a dead conn
        "echo": False,
        "connect_args": connect_args,
    }


def _make_engine() -> AsyncEngine:
    return create_async_engine(settings.database_url, **_engine_kwargs(settings.database_url))


engine: AsyncEngine = _make_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ── Database authority (#95, Batch 5 + independent-review correction) ────────
#
# TWO LAYERS, AND ONLY ONE OF THEM IS THE BOUNDARY.
#
# Layer 1 — THE CREDENTIAL (the boundary). ``settings.database_url`` is the
# restricted login ``muddato_api``: NOSUPERUSER, NOBYPASSRLS, **NOINHERIT**,
# owner of nothing, holder of no table or sequence privilege, and with no
# ``CREATE`` on schema ``public``. It DOES hold ``USAGE`` there — that resolves
# a name and grants access to nothing behind it, and having it makes every
# refusal a clean "permission denied" instead of a misleading "relation does
# not exist". It is a member of the authority roles, so it may ``SET ROLE`` to
# them, and inherits none of their privileges until it does. A connection that
# never assumes an authority — a health check, a stray ``engine.connect()``, a
# library with its own pool — can therefore read NOTHING. That is a property of
# the credential, not of this module, which is the point: the previous design
# connected as the schema owner and depended on the listener below to downgrade,
# so anything bypassing the listener bypassed row security entirely.
#
# The schema owner still exists and still runs migrations, under a separate
# ``MIGRATION_DATABASE_URL``. Neither credential falls back to the other in
# production (``app/config.py``, ``alembic/env.py``).
#
# Layer 2 — THE AUTHORITY (this module). Every transaction assumes a role at
# BEGIN, unconditionally:
#
#   AUTHORITY   role                effective reach
#   ─────────── ─────────────────── ────────────────────────────────────────────
#   WEBHOOK     muddato_webhook     NARROWEST. Not a member of muddato_runtime,
#                                   so NO table privilege at all — only EXECUTE
#                                   on the provider resolvers a public callback
#                                   needs before it has proved anything.
#   APP         muddato_app         DEFAULT. Tenant work, scoped by app.tenant_id;
#                                   with no scope it reads its OWN user row and
#                                   nothing else. This is what a route that forgot
#                                   resolve_tenant gets: zero tenant rows.
#   CUSTOMER    muddato_customer    A phone-verified person, across the shops they
#                                   buy from — keyed on app.user_id.
#   IDENTITY    muddato_identity    The pre-authentication bootstrap. Since
#                                   rg0credsplit it has NO policy on `users`:
#                                   its direct access to the directory is
#                                   deny-all, and the three narrow
#                                   `rls_identity_*` capability functions are
#                                   the only way in.
#   WORKER      muddato_worker      Scheduler, bot, cron. Cross-tenant.
#   PLATFORM    muddato_platform    Platform HQ. Cross-tenant.
#
# None of them is a superuser, has BYPASSRLS, or owns a table; elevation above
# APP is an explicit call, never an inference from absent information.
#
# THREE GUCs carry the scope, all transaction-local (``set_config(..., true)``)
# and all rewritten — including to the empty string — at the start of every
# transaction, so a pooled connection can never hand tenant A's scope to tenant
# B. ``SET LOCAL ROLE`` unwinds at COMMIT and at ROLLBACK for the same reason.
#
#   app.tenant_id      the resolved tenant
#   app.user_id        the authenticated account
#   app.invitation_id  one invitation, unlocked by presenting its raw credential
#
# Layer 2 is inert unless ``settings.rls_enabled``. Layer 1 is not: with the
# authority machinery switched off the restricted login simply reads nothing, so
# the failure mode is "denied", never "leaked".

_WEBHOOK_ROLE = "muddato_webhook"
_APP_ROLE = "muddato_app"
_CUSTOMER_ROLE = "muddato_customer"
_IDENTITY_ROLE = "muddato_identity"
_WORKER_ROLE = "muddato_worker"
_PLATFORM_ROLE = "muddato_platform"


class DbAuthority(StrEnum):
    """Who the database thinks this transaction is. Ordered least → most."""

    #: Narrower than `app`: not a member of `muddato_runtime`, so it holds NO
    #: privilege on any table. It exists for the handful of statements a public
    #: provider callback runs before it has proved anything — one call to a
    #: narrow resolver that returns just enough to check a signature.
    webhook = _WEBHOOK_ROLE
    app = _APP_ROLE
    customer = _CUSTOMER_ROLE
    identity = _IDENTITY_ROLE
    worker = _WORKER_ROLE
    platform = _PLATFORM_ROLE


#: The default is the LEAST privileged authority, so omission denies.
_db_authority: ContextVar[DbAuthority] = ContextVar(
    "db_authority", default=DbAuthority.app,
)
_request_tenant_id: ContextVar[str | None] = ContextVar("rls_tenant_id", default=None)
_request_user_id: ContextVar[str | None] = ContextVar("rls_user_id", default=None)
_request_invitation_id: ContextVar[str | None] = ContextVar(
    "rls_invitation_id", default=None,
)

#: The GUC each contextvar feeds, in the order they are written.
_SCOPE_GUCS: tuple[tuple[str, ContextVar[str | None]], ...] = (
    ("app.tenant_id", _request_tenant_id),
    ("app.user_id", _request_user_id),
    ("app.invitation_id", _request_invitation_id),
)


def _canonical_uuid(value: object) -> str | None:
    """``value`` as a canonical UUID string, or None if it is not one.

    Every id reaching a GUC passes through here, which is what makes the inline
    interpolation in the sync ``after_begin`` listener injection-safe — and what
    turns a malformed id into "no scope" (which denies) rather than a 22P02 the
    caller would see as a 500. The SQL accessors carry the same regex guard, so
    the two layers fail closed on exactly the same inputs.
    """
    if value is None:
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _scope_statements() -> list[str]:
    """The literal SQL that pins the current context onto an open transaction.

    Every GUC is written on every transaction — blank when unset — so nothing
    can survive from a previous checkout of the same pooled connection.
    """
    role = _db_authority.get().value
    out = [f"SET LOCAL ROLE {role}"]
    for guc, var in _SCOPE_GUCS:
        value = _canonical_uuid(var.get()) or ""
        out.append(f"SELECT set_config('{guc}', '{value}', true)")
    return out


def _rls_set_local_sql(tenant_id: str) -> list[tuple[str, dict[str, str]]]:
    """``(sql, params)`` pairs that put an OPEN transaction into APP authority
    scoped to ``tenant_id``.

    Retained for the isolation tests, which drive a raw session directly rather
    than going through the dependency chain. ``SET ... =`` cannot take a bind
    parameter and ``set_config`` can, which is what keeps the id injection-safe
    on this path.
    """
    return [
        (f"SET LOCAL ROLE {_APP_ROLE}", {}),
        ("SELECT set_config('app.tenant_id', :tid, true)", {"tid": str(tenant_id)}),
    ]


async def apply_db_scope(session: AsyncSession) -> None:
    """Pin the current authority + scope onto ``session``'s open transaction.

    Called whenever the context changes mid-request (a dependency resolves the
    tenant, a guard elevates to platform). The ``after_begin`` listener replays
    the same statements on every *later* transaction, so a mid-request commit
    cannot silently drop the scope.
    """
    if not settings.rls_enabled:
        return
    for sql in _scope_statements():
        await session.execute(text(sql))


async def set_tenant_scope(session: AsyncSession, tenant_id: object) -> None:
    """Scope the request to one tenant. Called by ``resolve_tenant``."""
    _request_tenant_id.set(_canonical_uuid(tenant_id))
    await apply_db_scope(session)


async def set_identity_scope(session: AsyncSession, user_id: object) -> None:
    """Record the authenticated account. Called by ``current_user``.

    This is what lets an as-yet-unscoped request read its own user row and its
    own memberships — the login bootstrap — without reopening the directory.
    """
    _request_user_id.set(_canonical_uuid(user_id))
    await apply_db_scope(session)


async def set_invitation_capability(session: AsyncSession, invitation_id: object) -> None:
    """Unlock exactly one invitation row, by id, for the rest of the request.

    The id is not guessable input: it comes back from ``rls_resolve_invitation``,
    which the caller reaches only by presenting the raw credential. There is no
    list access and no enumeration — the policy matches a single id.
    """
    _request_invitation_id.set(_canonical_uuid(invitation_id))
    await apply_db_scope(session)


async def use_authority(session: AsyncSession, authority: DbAuthority) -> None:
    """Assume ``authority`` for the rest of the request.

    Deliberately explicit and deliberately not reachable by accident: the only
    callers are the platform-HQ guard (after the platform role check has
    passed), the customer router (after the phone-proof gate), the identity
    bootstrap, and the worker/webhook entry points.
    """
    _db_authority.set(authority)
    await apply_db_scope(session)


@asynccontextmanager
async def capability(
    session: AsyncSession, authority: DbAuthority,
) -> AsyncIterator[AsyncSession]:
    """Borrow ``authority`` for one call, then put it back — exactly.

    ``use_authority`` is a one-way door: it changes the authority for the rest of
    the request. That is right for a router-level grant made after an
    authorization check, and wrong for the two places that must reach past their
    own authority for a single statement and then return to it:

    * a public provider webhook resolving which merchant signed the request,
      BEFORE the signature has been checked;
    * the login bootstrap finding or creating an account, BEFORE anyone is
      signed in.

    Both borrow a narrow authority whose only privilege is EXECUTE on one
    capability function. Restoring on the way out — including on an exception —
    is what stops a handler that raises mid-bootstrap from finishing the request
    with elevated reach.

    Yields the same session; the ``with`` form exists to make the restore
    unmissable, not to hand out a different object.
    """
    previous = _db_authority.get()
    await use_authority(session, authority)
    body_failed = False
    try:
        yield session
    except BaseException:
        body_failed = True
        raise
    finally:
        # The context variable first, and unconditionally. That is what actually
        # restores the authority: ``after_begin`` reads it at the start of every
        # transaction, so even if the statement below never runs, the next one
        # is correct.
        _db_authority.set(previous)
        try:
            await apply_db_scope(session)
        except Exception:
            if not body_failed:
                raise
            # The body already failed, which means the transaction is aborted,
            # which means this statement was always going to fail too — with
            # "current transaction is aborted", REPLACING the real error. An
            # operator would see that instead of "permission denied for table
            # users", which is the difference between diagnosing a capability in
            # five minutes and looking in the wrong place for an hour. The
            # authority is already restored above, so there is nothing left to
            # protect by raising here.
            pass


def reset_db_scope() -> None:
    """Forget authority and scope. Tests and long-lived tasks call this between
    units of work; a normal request does not need it, because each request runs
    in its own task with its own copy of the context."""
    _db_authority.set(DbAuthority.app)
    _request_tenant_id.set(None)
    _request_user_id.set(None)
    _request_invitation_id.set(None)


def current_db_scope() -> dict[str, str | None]:
    """The active authority + scope, for assertions and structured logs."""
    return {
        "authority": _db_authority.get().value,
        "tenant_id": _request_tenant_id.get(),
        "user_id": _request_user_id.get(),
        "invitation_id": _request_invitation_id.get(),
    }


@asynccontextmanager
async def authority_session(
    authority: DbAuthority, *, tenant_id: object = None, factory: Any = None,
) -> AsyncIterator[AsyncSession]:
    """A session that runs under ``authority`` from its first statement.

    For work with no HTTP request behind it — the scheduler, the bot, cron. The
    context is restored on exit, so a worker session can never leave its
    authority behind for whatever the task does next (a background job and a
    request handler can share a task in the web process when RUN_SCHEDULER is
    on, which is exactly how authority would otherwise leak).
    """
    tokens = (
        _db_authority.set(authority),
        _request_tenant_id.set(_canonical_uuid(tenant_id)),
        _request_user_id.set(None),
        _request_invitation_id.set(None),
    )
    try:
        async with (factory or SessionLocal)() as session:
            yield session
    finally:
        _db_authority.reset(tokens[0])
        _request_tenant_id.reset(tokens[1])
        _request_user_id.reset(tokens[2])
        _request_invitation_id.reset(tokens[3])


def worker_session(*, tenant_id: object = None, factory: Any = None) -> Any:
    """``authority_session(WORKER)`` — the scheduler/bot/cron entry point.

    ``factory`` exists so a caller can keep its own patchable session-factory
    seam (``app.services.scheduler.SessionLocal``, which the late-fee and cron
    tests swap for an engine bound to the test database).
    """
    return authority_session(DbAuthority.worker, tenant_id=tenant_id, factory=factory)


@event.listens_for(Session, "after_begin")
def _rls_reapply_on_begin(session: object, transaction: object, connection: Any) -> None:
    """Re-pin authority + scope whenever a transaction begins.

    Runs synchronously inside the async greenlet, so it can emit SQL on the sync
    connection proxy. It is what makes the guarantee unconditional: a request
    that commits mid-way starts a fresh transaction whose SET LOCAL state is
    gone, and without this the next statement would run as the *connection*
    role — the schema owner — and bypass RLS entirely.

    FAIL-CLOSED CONTRACT — verified, do not weaken:
      * RLS off → no-op (the layer is inert unless ``rls_enabled``).
      * No context at all → ``SET LOCAL ROLE muddato_app`` with all three GUCs
        blank. That is the *denying* posture: zero tenant rows, no writes. It is
        what a route that forgot ``resolve_tenant`` gets, and it is the whole
        point of Batch 5 — the old code returned early here and left the
        transaction running as the owner.
      * A malformed id is normalised to blank by ``_canonical_uuid`` before it
        can be interpolated, so it neither injects nor widens.
    """
    if not settings.rls_enabled:
        return
    for sql in _scope_statements():
        connection.exec_driver_sql(sql)


def new_uuid() -> str:
    """String UUIDs to match the JSON wire format and Supabase conventions."""
    return str(uuid4())


#: Retained name for the pre-Batch-5 call sites and tests that import it.
_apply_rls_scope = set_tenant_scope


__all__: tuple[str, ...] = (
    "Base",
    "DbAuthority",
    "SessionLocal",
    "_apply_rls_scope",
    "apply_db_scope",
    "authority_session",
    "capability",
    "current_db_scope",
    "engine",
    "get_db",
    "new_uuid",
    "reset_db_scope",
    "set_identity_scope",
    "set_invitation_capability",
    "set_tenant_scope",
    "use_authority",
    "worker_session",
)


# Convenience re-export so `from app.db import Any` doesn't surprise import sorters.
_AnyMarker: Any = None
