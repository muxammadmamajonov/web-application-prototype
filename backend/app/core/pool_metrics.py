"""Periodic DB/Redis connection-pool sampling (MUDD-89).

``app/core/metrics.py`` already defined ``record_db_pool_stats`` /
``record_redis_pool_stats`` and the gauges they feed — but nothing ever called
them. Every scrape of ``/metrics`` read their zero default, so the MUDD-86 load
baseline had to fall back to a manual ``pg_stat_activity`` + ``ps -o pcpu`` to
find the actual root cause (uvicorn worker at 96.3% CPU while 9 of 10 pooled DB
connections sat "idle in transaction"). This module is what makes that signal
visible on an ongoing basis instead of only reproducible by hand.

Two distinct samples, at two different costs:

* ``_sample_sqlalchemy_pool`` / ``_sample_redis_pool`` read pure in-memory pool
  counters (``Pool.checkedout()``, the redis-py connection pool's in-use set) —
  free, no I/O, safe to call often.
* ``_sample_idle_in_transaction`` needs a live query against Postgres
  (``pg_stat_activity``). It deliberately does NOT borrow a connection from the
  app's own request-serving pool (``app.db.engine``) — sampling from the same
  pool that's being observed means the one moment the pool is fully saturated
  (exactly what we need to see) is the moment the sampler itself would block or
  time out waiting for a connection. Instead it keeps one small, dedicated
  engine (``pool_size=1``) open for the lifetime of the process, the same way a
  separate monitoring connection would work against any other database.

Defensive by construction, matching the rest of ``app/core``: any failure here
is logged at debug and never raised — a broken sampler must not affect request
serving or process startup/shutdown.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings
from app.core.metrics import (
    record_db_idle_in_transaction,
    record_db_pool_stats,
    record_redis_pool_stats,
)

log = logging.getLogger("muddato.pool_metrics")

#: How often to sample. Independent of request traffic — an idle app between
#: scrapes still reports a fresh reading, and a wedged app (no new requests
#: completing) keeps reporting too, since this loop is not on the request path.
SAMPLE_INTERVAL_SECONDS = 15.0

_monitor_engine: AsyncEngine | None = None


def _get_monitor_engine() -> AsyncEngine:
    """A single dedicated connection for the ``pg_stat_activity`` sample —
    never the app's request-serving pool (see module docstring)."""
    global _monitor_engine
    if _monitor_engine is None:
        _monitor_engine = create_async_engine(
            settings.database_url,
            pool_size=1,
            max_overflow=0,
            pool_pre_ping=True,
            connect_args={"command_timeout": 5, "statement_cache_size": 0},
        )
    return _monitor_engine


def _sample_sqlalchemy_pool() -> None:
    try:
        from app.db import engine

        # engine.pool is typed as the base `Pool`; checkedout() is a QueuePool
        # method (the concrete pool class create_async_engine uses by default).
        checked_out = engine.pool.checkedout()  # type: ignore[attr-defined]
        capacity = settings.db_pool_size + settings.db_max_overflow
        record_db_pool_stats(checked_out, capacity)
    except Exception:
        log.debug("pool_metrics.sqlalchemy_sample_failed", exc_info=True)


def _sample_redis_pool() -> None:
    try:
        from app.core.redis import get_redis

        cpool = get_redis().connection_pool
        in_use = len(getattr(cpool, "_in_use_connections", ()))
        capacity = getattr(cpool, "max_connections", 0) or 0
        record_redis_pool_stats(in_use, capacity)
    except Exception:
        log.debug("pool_metrics.redis_sample_failed", exc_info=True)


async def _sample_idle_in_transaction() -> None:
    """Count this app's Postgres backends by ``state`` and publish the
    idle-in-transaction share — the MUDD-86 root-cause signal.

    Scoped to ``usename = current_user`` so a shared Postgres instance (several
    apps/roles on one server) doesn't fold unrelated connections into this app's
    saturation reading, and ``pid <> pg_backend_pid()`` excludes the sampling
    connection's own (necessarily "active") row.
    """
    try:
        engine = _get_monitor_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT state, count(*)::int AS n FROM pg_stat_activity "
                    "WHERE datname = current_database() AND usename = current_user "
                    "AND pid <> pg_backend_pid() GROUP BY state"
                )
            )
            counts = {(row.state or "unknown"): row.n for row in result}
        idle_in_txn = counts.get("idle in transaction", 0) + counts.get(
            "idle in transaction (aborted)", 0,
        )
        total = sum(counts.values())
        record_db_idle_in_transaction(idle_in_txn, total)
    except Exception:
        log.debug("pool_metrics.idle_in_transaction_sample_failed", exc_info=True)


async def _loop() -> None:
    while True:
        await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)
        _sample_sqlalchemy_pool()
        _sample_redis_pool()
        await _sample_idle_in_transaction()


def start() -> asyncio.Task[None] | None:
    """Start the periodic sampler as a background task. Returns the task (for
    ``stop()`` to cancel), or None if it couldn't be started — a sampler that
    fails to boot must never take the app down with it."""
    try:
        return asyncio.create_task(_loop(), name="pool-metrics-sampler")
    except Exception:
        log.exception("pool_metrics.start_failed")
        return None


async def stop(task: asyncio.Task[None] | None) -> None:
    """Cancel the sampler task and dispose the dedicated monitoring engine."""
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    global _monitor_engine
    if _monitor_engine is not None:
        try:
            await _monitor_engine.dispose()
        except Exception:
            log.debug("pool_metrics.monitor_engine_dispose_failed", exc_info=True)
        _monitor_engine = None
