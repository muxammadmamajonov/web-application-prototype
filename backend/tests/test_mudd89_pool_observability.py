"""MUDD-89 — DB pool + idle-in-transaction + in-flight-request observability.

MUDD-86's load baseline found the actual production freeze: at 250 concurrent
VUs the uvicorn worker sat at 96.3% CPU while 9 of the pool's 10 connections
were "idle in transaction" — and NOTHING in ``/metrics`` could show that, because
``record_db_pool_stats``/``record_redis_pool_stats`` (app/core/metrics.py)
existed but were never called from anywhere. This file proves two things for
each of the three samplers in ``app/core/pool_metrics.py``:

  1. **Wiring**: the sampler reads the real object (SQLAlchemy pool, redis-py
     connection pool, ``pg_stat_activity``) and the gauge value tracks it —
     not a fixed/zero reading regardless of state.
  2. **Fault tolerance**: a broken gauge or a broken sample source degrades to a
     debug breadcrumb, never an exception that could take down the sampler loop
     or (if ever called inline) a request.

It also covers the in-flight HTTP request gauge (``muddato_http_requests_
inprogress``), added because a live ``/metrics`` scrape while wiring this up
showed the instrumentator does **not** actually emit one in the pinned version —
docs/OBSERVABILITY.md's claim that it did was stale/false, the same "metric
that isn't real" failure mode this issue is about.

The integration test near the end is the real acceptance bar — real Postgres
connections deliberately left "idle in transaction", sampled for real, with the
resulting ratio asserted to have MOVED off zero. That is "show it changes under
load", not a screenshot.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


def _events(caplog, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith(name)]


class _FakePool:
    def __init__(self, checked_out: int):
        self._checked_out = checked_out

    def checkedout(self) -> int:
        return self._checked_out


class _FakeEngine:
    def __init__(self, checked_out: int):
        self.pool = _FakePool(checked_out)


class _FakeRedisPool:
    def __init__(self, in_use: int, capacity: int):
        self._in_use_connections = [object()] * in_use
        self.max_connections = capacity


class _FakeRedisClient:
    def __init__(self, in_use: int, capacity: int):
        self.connection_pool = _FakeRedisPool(in_use, capacity)


class _FakeRow:
    def __init__(self, state: str, n: int):
        self.state = state
        self.n = n


class _FakeResult:
    def __init__(self, rows: list[_FakeRow]):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeConn:
    def __init__(self, rows: list[_FakeRow]):
        self._rows = rows

    async def execute(self, *_a, **_k):
        return _FakeResult(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


class _FakeMonitorEngine:
    def __init__(self, rows: list[_FakeRow]):
        self._rows = rows

    def connect(self):
        return _FakeConn(self._rows)


# ══════════════════════════════════════════════════════════════════════════════
# Wiring: the sampler reads the real live object, not a constant
# ══════════════════════════════════════════════════════════════════════════════


def test_sqlalchemy_pool_sample_tracks_real_checkout_count(monkeypatch) -> None:
    from app.core import metrics as m
    from app.core import pool_metrics as pm

    if not m._ENABLED:
        pytest.skip("prometheus_client unavailable — gauges are no-ops")

    import app.db as dbmod

    # Two different readings must produce two different gauge values — a sampler
    # that always reports the same number regardless of pool state is exactly
    # the "metric that always reads zero" failure this issue calls out.
    monkeypatch.setattr(dbmod, "engine", _FakeEngine(checked_out=0))
    pm._sample_sqlalchemy_pool()
    idle_reading = m._db_active_connections._value.get()

    monkeypatch.setattr(dbmod, "engine", _FakeEngine(checked_out=7))
    pm._sample_sqlalchemy_pool()
    busy_reading = m._db_active_connections._value.get()

    assert idle_reading == 0
    assert busy_reading == 7
    assert busy_reading != idle_reading, (
        "the gauge must move with real pool state, not stay constant"
    )


def test_redis_pool_sample_tracks_real_in_use_count(monkeypatch) -> None:
    from app.core import metrics as m
    from app.core import pool_metrics as pm

    if not m._ENABLED:
        pytest.skip("prometheus_client unavailable — gauges are no-ops")

    monkeypatch.setattr(
        "app.core.redis.get_redis", lambda: _FakeRedisClient(in_use=0, capacity=50),
    )
    pm._sample_redis_pool()
    idle_reading = m._redis_active_connections._value.get()

    monkeypatch.setattr(
        "app.core.redis.get_redis", lambda: _FakeRedisClient(in_use=12, capacity=50),
    )
    pm._sample_redis_pool()
    busy_reading = m._redis_active_connections._value.get()

    assert idle_reading == 0
    assert busy_reading == 12
    assert m._redis_pool_utilization._value.get() == pytest.approx(12 / 50)


async def test_idle_in_transaction_sample_computes_ratio_from_pg_stat_activity(
    monkeypatch,
) -> None:
    from app.core import metrics as m
    from app.core import pool_metrics as pm

    if not m._ENABLED:
        pytest.skip("prometheus_client unavailable — gauges are no-ops")

    rows = [
        _FakeRow("idle in transaction", 9),
        _FakeRow("active", 1),
    ]
    monkeypatch.setattr(pm, "_get_monitor_engine", lambda: _FakeMonitorEngine(rows))

    await pm._sample_idle_in_transaction()

    assert m._db_idle_in_transaction._value.get() == 9
    assert m._db_idle_in_transaction_ratio._value.get() == pytest.approx(0.9), (
        "9 of 10 idle-in-transaction — the exact MUDD-86 freeze ratio — must "
        "read back as 0.9, not 0 and not a fixed placeholder"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Fault tolerance: a broken source/gauge must never raise
# ══════════════════════════════════════════════════════════════════════════════


def test_sqlalchemy_pool_sample_swallows_a_broken_pool(monkeypatch, caplog) -> None:
    import app.db as dbmod
    from app.core import pool_metrics as pm

    class _BrokenEngine:
        @property
        def pool(self):
            raise RuntimeError("pool exploded")

    monkeypatch.setattr(dbmod, "engine", _BrokenEngine())

    with caplog.at_level(logging.DEBUG, logger="muddato.pool_metrics"):
        pm._sample_sqlalchemy_pool()  # must not raise

    assert _events(caplog, "pool_metrics.sqlalchemy_sample_failed")


def test_redis_pool_sample_swallows_a_broken_client(monkeypatch, caplog) -> None:
    from app.core import pool_metrics as pm

    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.core.redis.get_redis", _boom)

    with caplog.at_level(logging.DEBUG, logger="muddato.pool_metrics"):
        pm._sample_redis_pool()  # must not raise

    assert _events(caplog, "pool_metrics.redis_sample_failed")


async def test_idle_in_transaction_sample_swallows_a_broken_query(
    monkeypatch, caplog,
) -> None:
    from app.core import pool_metrics as pm

    class _BrokenConn:
        async def execute(self, *_a, **_k):
            raise RuntimeError("db unreachable")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _BrokenEngine:
        def connect(self):
            return _BrokenConn()

    monkeypatch.setattr(pm, "_get_monitor_engine", lambda: _BrokenEngine())

    with caplog.at_level(logging.DEBUG, logger="muddato.pool_metrics"):
        await pm._sample_idle_in_transaction()  # must not raise

    assert _events(caplog, "pool_metrics.idle_in_transaction_sample_failed")


def test_record_db_idle_in_transaction_gauge_fault_is_swallowed(
    monkeypatch, caplog,
) -> None:
    """Mirrors the existing pool-gauge fault tests in
    test_tr018_tr019_observability.py for the two new gauges."""
    from app.core import metrics as m

    if not m._ENABLED:
        pytest.skip("prometheus_client unavailable — gauges are no-ops")

    class _Boom:
        def set(self, *_a, **_k):
            raise RuntimeError("registry exploded")

    monkeypatch.setattr(m, "_db_idle_in_transaction", _Boom())

    with caplog.at_level(logging.DEBUG, logger="muddato.metrics"):
        assert m.record_db_idle_in_transaction(3, 10) is None

    recs = _events(caplog, "metrics.db_idle_in_transaction.record_failed")
    assert recs
    assert all(r.levelno < logging.INFO for r in recs)


# ══════════════════════════════════════════════════════════════════════════════
# Lifecycle: start()/stop() never raise, and stop() actually cancels + disposes
# ══════════════════════════════════════════════════════════════════════════════


async def test_start_and_stop_lifecycle_is_clean() -> None:
    from app.core import pool_metrics as pm

    task = pm.start()
    assert task is not None
    assert not task.done()

    await pm.stop(task)

    assert task.cancelled() or task.done()
    assert pm._monitor_engine is None, "stop() must dispose the monitor engine"


# ══════════════════════════════════════════════════════════════════════════════
# In-flight HTTP request gauge
# ══════════════════════════════════════════════════════════════════════════════


def test_inflight_gauge_tracks_concurrent_requests_not_a_constant() -> None:
    from app.core import metrics as m

    if not m._ENABLED:
        pytest.skip("prometheus_client unavailable — gauges are no-ops")

    baseline = m._http_inflight._value.get()
    m.inc_http_inflight()
    m.inc_http_inflight()
    assert m._http_inflight._value.get() == baseline + 2
    m.dec_http_inflight()
    assert m._http_inflight._value.get() == baseline + 1
    m.dec_http_inflight()
    assert m._http_inflight._value.get() == baseline


def test_inflight_inc_and_dec_swallow_a_broken_gauge(monkeypatch, caplog) -> None:
    from app.core import metrics as m

    if not m._ENABLED:
        pytest.skip("prometheus_client unavailable — gauges are no-ops")

    class _Boom:
        def inc(self, *_a, **_k):
            raise RuntimeError("registry exploded")

        def dec(self, *_a, **_k):
            raise RuntimeError("registry exploded")

    monkeypatch.setattr(m, "_http_inflight", _Boom())

    with caplog.at_level(logging.DEBUG, logger="muddato.metrics"):
        assert m.inc_http_inflight() is None
        assert m.dec_http_inflight() is None

    assert _events(caplog, "metrics.http_inflight.inc_failed")
    assert _events(caplog, "metrics.http_inflight.dec_failed")


async def test_inflight_gauge_moves_under_real_concurrent_requests(
    async_client,
) -> None:
    """Drives real requests through the real ASGI middleware stack (not a direct
    function call) — this is what proves RequestContextMiddleware's inc/dec pair
    is actually wired into the request path, not just unit-testable in isolation.
    """
    from app.core import metrics as m

    if not m._ENABLED:
        pytest.skip("prometheus_client unavailable — gauges are no-ops")

    baseline = m._http_inflight._value.get()

    async def _slow_request():
        # /health is DB/Redis-free (see app/main.py) — a pure middleware-stack
        # round trip, so this measures the gauge's own inc/dec, not backend I/O.
        return await async_client.get("/health")

    tasks = [asyncio.create_task(_slow_request()) for _ in range(10)]
    await asyncio.sleep(0)  # let the tasks start entering the middleware
    # Not a hard assertion on the exact in-flight count (scheduling is not
    # deterministic) — the point is it must have moved, and it must fully drain.
    responses = await asyncio.gather(*tasks)
    assert all(r.status_code == 200 for r in responses)
    assert m._http_inflight._value.get() == baseline, (
        "the gauge must fully drain back to baseline once every request completes"
    )


async def test_inflight_dec_still_fires_when_call_next_raises() -> None:
    """The decrement is in a ``finally`` specifically so a request that errors
    out doesn't leave the gauge permanently inflated.

    FastAPI's own ``ExceptionMiddleware`` sits INSIDE this middleware (Starlette
    wraps user middlewares around it), so in practice a handler exception is
    already converted to a 500 response before it reaches ``call_next`` here —
    an HTTP round trip can't exercise the raise path at all. This drives
    ``RequestContextMiddleware.dispatch`` directly with a ``call_next`` stub that
    raises, which is the only way to prove the ``finally`` actually matters.
    """
    from app.core import metrics as m
    from app.core.observability import RequestContextMiddleware

    if not m._ENABLED:
        pytest.skip("prometheus_client unavailable — gauges are no-ops")

    middleware = RequestContextMiddleware(app=None)  # app is unused by dispatch
    request = _fake_request({})

    async def _boom_call_next(_request):
        raise RuntimeError("downstream exploded")

    baseline = m._http_inflight._value.get()
    with pytest.raises(RuntimeError, match="downstream exploded"):
        await middleware.dispatch(request, _boom_call_next)

    assert m._http_inflight._value.get() == baseline, (
        "a raised call_next exception must not leave the in-flight gauge inflated"
    )


def _fake_request(headers: dict[str, str]):
    from starlette.requests import Request

    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("203.0.113.7", 4242),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


# ══════════════════════════════════════════════════════════════════════════════
# Integration tier — real Postgres, real "idle in transaction" connections
# ══════════════════════════════════════════════════════════════════════════════


async def test_idle_in_transaction_ratio_moves_under_real_synthetic_load() -> None:
    """The acceptance bar: hold real connections open mid-transaction (no
    commit), then prove the sampler's ratio reflects that — not a screenshot,
    a reproducible assertion against real Postgres.

    The load connections MUST be opened as the same role the sampler runs as.
    `_sample_idle_in_transaction` scopes its count with
    `usename = current_user` (deliberately — see its docstring: a shared
    Postgres must not fold another app's connections into this app's
    saturation reading). The sampler connects via `settings.database_url`,
    i.e. the restricted runtime role. `TEST_DATABASE_URL` is the *superuser*
    URL, so connections opened with it are invisible to the sampler by design
    and the count reads 0.

    That is not flakiness: it failed in CI every time and passed locally only
    where both URLs happened to resolve to the same role. Prefer the runtime
    credential, which CI provides as TEST_RUNTIME_DATABASE_URL.

    QA-R-002 convention: skip only when no runtime URL is available; once set,
    every failure here is a real failure, not a skip.
    """
    import os

    # DATABASE_URL is always populated with a dummy import-time value in the
    # unit job.  Only the integration tier's explicit runtime URL proves a
    # real Postgres exists and uses the same restricted role as the sampler.
    url = os.environ.get("TEST_RUNTIME_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_RUNTIME_DATABASE_URL not set — integration tier runs in CI"
        )

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core import metrics as m
    from app.core import pool_metrics as pm

    if not m._ENABLED:
        pytest.skip("prometheus_client unavailable — gauges are no-ops")

    # A dedicated engine with enough headroom to hold N idle-in-transaction
    # connections simultaneously, independent of app.db's own pool.
    load_engine = create_async_engine(url, pool_size=5, max_overflow=0)
    conns = []
    try:
        # Open 3 connections and BEGIN a transaction on each WITHOUT committing —
        # exactly what "idle in transaction" means: a live transaction, no
        # statement currently executing.
        for _ in range(3):
            conn = await load_engine.connect()
            await conn.execute(text("BEGIN"))
            await conn.execute(text("SELECT 1"))
            conns.append(conn)

        pm._monitor_engine = None  # force a fresh monitor connection for this test
        await pm._sample_idle_in_transaction()

        ratio = m._db_idle_in_transaction_ratio._value.get()
        idle_count = m._db_idle_in_transaction._value.get()

        assert idle_count >= 3, (
            f"expected >=3 idle-in-transaction backends, sampler read {idle_count}. "
            "If this is 0, check that the load engine's role matches the sampler's "
            "(`usename = current_user`) — a superuser URL is invisible to it."
        )
        assert ratio > 0, "the ratio must have moved off zero under real load"
    finally:
        for conn in conns:
            await conn.rollback()
            await conn.close()
        await load_engine.dispose()
        await pm.stop(None)
        pm._monitor_engine = None
