"""The jobstore must assume ``muddato_worker`` in a way a pooler cannot discard.

This is the regression test for the outage of 2026-08-18: ``rf0failclosed``
revoked ``apscheduler_jobs`` from the restricted runtime login and granted it to
``muddato_worker``, and ``make_jobstore_engine`` assumed that role with the
libpq ``options='-c role=...'`` startup parameter. A direct connection honours
that parameter, so every test and every developer laptop agreed the fix worked.
Production does not use a direct connection — it goes through Supabase's
Supavisor pooler, which drops unrecognised startup parameters WITHOUT error. The
scheduler came up as the restricted login, hit ``permission denied for table
apscheduler_jobs`` and died at boot, silently, for twenty-one hours: nothing
that was being monitored touches the ``worker`` process.

The mechanism is therefore the thing under test, not just the outcome. A test
that only asserts ``current_role`` against a direct test database would have
passed on the broken code — which is exactly what happened.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.config import settings
from app.core.rls_manifest import ROLE_WORKER
from app.services.scheduler import make_jobstore_engine


@pytest.fixture
def rls_engine():
    """The engine as production builds it.

    ``RLS_ENABLED`` is the switch that decides whether the worker authority is
    assumed at all, and it is false in a bare test environment. Reading it from
    the ambient environment would make both proofs below pass vacuously — which
    is one of the reasons the original defect shipped.
    """
    with patch.object(settings, "rls_enabled", True):
        yield make_jobstore_engine()


# A test asserting ``"options" not in dialect.create_connect_args(url)`` was
# written first and deleted: ``create_connect_args`` is built from the URL alone
# and never sees the engine-level ``connect_args``, so it passed just as happily
# on the broken code. Keeping it would have repeated the exact mistake this file
# exists to document — a check that cannot fail. The proof below can.


def test_worker_role_is_assumed_with_a_statement_on_every_new_connection(
    rls_engine,
) -> None:
    """``SET ROLE`` is ordinary SQL, so it survives any pooler in between."""
    engine = rls_engine
    # ``connect`` is a Pool event: listening on an Engine registers it on that
    # engine's pool. The psycopg dialect registers its own listeners here too;
    # only ours is under test, so filter by defining module rather than driving
    # driver internals with a stub.
    hooks = [
        fn
        for fn in getattr(engine.pool.dispatch, "connect", [])
        if getattr(fn, "__module__", "") == "app.services.scheduler"
    ]
    assert hooks, (
        "the jobstore engine registers no connect-time hook of its own, so "
        "nothing assumes the worker authority: the connection stays on the "
        "NOINHERIT runtime login and apscheduler_jobs is refused"
    )

    executed: list[str] = []

    class _Cursor:
        def execute(self, sql: str, *_args: object) -> None:
            executed.append(sql)

        def close(self) -> None:
            pass

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

    for fn in hooks:
        fn(_Conn(), None)

    assert any(f"SET ROLE {ROLE_WORKER}" in sql for sql in executed), (
        f"the connect hook never assumed {ROLE_WORKER}; it ran {executed!r}"
    )


# A third test opened a real connection and asserted ``current_role`` and
# ``has_table_privilege`` against ``TEST_DATABASE_URL``. It is gone, and the
# reason is worth keeping: the integration cluster's schema is built from ORM
# metadata, so ``apscheduler_jobs`` (raw DDL in migration ``e5f6``) is absent,
# and its authority roles exist without the production grants — ``SET ROLE
# muddato_worker`` there lands on a role with no USAGE on ``public``. The test
# therefore went red on a perfectly healthy cluster, which is the worst kind of
# test: it reports the environment, not the code.
#
# It would also not have caught the defect it was written for. The grants were
# always right; what failed was the mechanism that assumed the role, and that
# only fails when a pooler sits in the path — which no test cluster has. The
# proofs above test the mechanism directly and do catch it.
#
# The place a live version belongs is ``app/cli/rls_preflight``, which already
# runs against the real database on every deploy and would see the real pooler.
# Tracked in MUDD-52.
