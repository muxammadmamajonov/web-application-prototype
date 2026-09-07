"""Serialize every schema-mutating fixture against `TEST_DATABASE_URL`.

Three independent fixtures — ``migrated_db``, ``isolation_ctx`` and
``rls_world`` (conftest.py, `_rls_world.py`) — each ``DROP SCHEMA IF EXISTS
public CASCADE; CREATE SCHEMA public`` against the same physical database, and
``migrated_db`` / ``rls_world`` additionally run ``alembic upgrade head``,
whose migrations take an ``AccessExclusiveLock`` per table via ``ALTER TABLE
... ENABLE ROW LEVEL SECURITY``.

`bb0bbafc` added a `pg_advisory_lock` around `migrated_db`'s own upgrade step
only. That was not enough: CI run 33167077131 reproduced the identical
`DeadlockDetected` (PIDs 2080/2081, `AccessExclusiveLock` vs a `ShareLock` on
the other's transaction) at the setup of
`test_replacement_editor_is_intact_and_still_guards_payments` with
pytest-xdist NOT running — a plain sequential suite. The only way two
Postgres backends can hold conflicting locks in a nominally sequential run is
if a schema reset from one of the OTHER unlocked fixtures (`isolation_ctx`,
`rls_world`, or `migrated_db`'s own teardown) overlapped a migration's DDL.
Locking only the alembic step left the DROP/CREATE statements — which
themselves need an AccessExclusiveLock on every relation in `public` — free
to race any of the other three.

Every schema-mutating statement in all three fixtures now takes the SAME
advisory lock key before touching `public`, so at most one of
{migrated_db setup, migrated_db teardown, isolation_ctx setup, isolation_ctx
teardown, rls_world setup, rls_world teardown} runs at a time regardless of
which one it is or what triggers the overlap.

POLLING, NOT BLOCKING (this is the part that actually took two attempts to
get right — reproduced locally with two real concurrent processes, not
guessed): the first version of this lock used the blocking `pg_advisory_lock`
call directly, same as `bb0bbafc`. That reintroduced a livelock Postgres's own
deadlock detector CANNOT see. One migration runs `CREATE INDEX CONCURRENTLY`
(`uq_payments_external_ref`), which must wait for every OTHER open transaction
in the cluster to finish — not just ones touching its table — before it can
complete. A session blocked inside `pg_advisory_lock(...)` is *mid-statement*,
which Postgres treats as an open transaction for the duration of the wait,
`autocommit` or not. So: holder A runs `CREATE INDEX CONCURRENTLY`, which
waits on waiter B's blocked lock-acquisition transaction to end; B is blocked
waiting for A's advisory lock, which A won't release until its migration
(i.e. the CONCURRENTLY step) finishes. A → B → A. Postgres's deadlock detector
tracks heavyweight lock waits, not "waiting for the xmin horizon to advance",
so this cycle never fires a `DeadlockDetected` — it just hangs forever.
`pg_try_advisory_lock`, polled with a real (non-transactional, autocommit)
gap between attempts, never leaves a transaction open while waiting, so it
can't participate in that cycle.
"""

from __future__ import annotations

import asyncio
import time
import warnings
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# The exact id `test_mudd95_migration_advisory_lock.py` exercises — keep it in
# sync with that test if this ever changes.
SCHEMA_LOCK_KEY = 1

_POLL_INTERVAL_S = 0.05
_LOCK_TIMEOUT_S = 60.0


def _connect(migration_url_sync: str):
    import psycopg

    # psycopg.connect() speaks libpq conninfo, not the SQLAlchemy
    # "postgresql+psycopg://" URL — the "+psycopg" driver marker must be
    # stripped or connect() raises immediately.
    #
    # autocommit=True: every statement on this connection (the try-lock polls,
    # the final unlock) commits itself immediately, so it never sits "idle in
    # transaction" — see the module docstring for why that matters here.
    lock_url = migration_url_sync.replace("+psycopg", "", 1)
    return psycopg.connect(lock_url, autocommit=True)


def _try_acquire_once(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (SCHEMA_LOCK_KEY,))
        return bool(cur.fetchone()[0])


def acquire_sync(migration_url_sync: str):
    """Connect and poll-acquire the lock; returns the holding connection.

    Falls back to ``None`` (unprotected) if no DB is reachable at all — e.g.
    the sync driver unavailable locally — matching `bb0bbafc`'s original
    fail-open-with-a-warning behaviour rather than masking a real DB failure.
    """
    try:
        conn = _connect(migration_url_sync)
    except Exception as exc:
        warnings.warn(
            f"schema-serialization advisory lock unavailable ({exc!r}); "
            "proceeding without serialization against concurrent migrators",
            stacklevel=2,
        )
        return None

    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while not _try_acquire_once(conn):
        if time.monotonic() >= deadline:
            conn.close()
            raise TimeoutError(
                f"could not acquire the schema-serialization advisory lock "
                f"(key {SCHEMA_LOCK_KEY}) within {_LOCK_TIMEOUT_S}s — a "
                "holder is likely stuck"
            )
        time.sleep(_POLL_INTERVAL_S)
    return conn


def release_sync(conn) -> None:
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_KEY,))
    finally:
        conn.close()


@asynccontextmanager
async def schema_lock(migration_url_sync: str):
    """Hold the shared advisory lock for the duration of the `async with` body.

    Runs the (blocking, thread-pool) acquire/release on the sync lock
    connection via `asyncio.to_thread` so the event loop stays free — the
    poll loop itself never blocks the loop, only the worker thread.
    """
    conn = await asyncio.to_thread(acquire_sync, migration_url_sync)
    try:
        yield
    finally:
        await asyncio.to_thread(release_sync, conn)


async def reset_public_schema(engine: AsyncEngine, migration_url_sync: str) -> None:
    """DROP+CREATE the public schema on `engine`, serialized via `schema_lock`."""
    async with schema_lock(migration_url_sync), engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))


def run_locked_migration_sync(migration_url_sync: str) -> None:
    """Run `alembic upgrade head` while holding the shared advisory lock.

    Meant to be called via `asyncio.to_thread` — everything here, including
    the poll loop, is synchronous and runs off the event loop.
    """
    from alembic.config import Config

    from alembic import command

    conn = acquire_sync(migration_url_sync)
    try:
        # Migration failures must propagate as test failures (QA-R-002), not
        # be swallowed into a silent unprotected retry.
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        release_sync(conn)
