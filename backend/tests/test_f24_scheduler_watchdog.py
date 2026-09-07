"""(F-24) Scheduler heartbeat + watchdog.

The worker must detect the scheduler dying *between* fire times and exit non-zero
so the orchestrator restarts it — per-job success gauges only catch a job that
errors, not the scheduler process going silently dead. These tests drive the
``_watchdog`` loop directly (no real APScheduler, no DB) by patching
``get_scheduler`` to report a (not-)running scheduler.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app import worker


async def test_watchdog_exits_when_scheduler_not_running() -> None:
    """A dead scheduler (running=False) → watchdog returns True (caller exits 1)."""
    stop = asyncio.Event()
    with patch.object(worker, "get_scheduler", return_value=SimpleNamespace(running=False)):
        failed = await asyncio.wait_for(worker._watchdog(stop), timeout=2)
    assert failed is True
    # It also requests shutdown so the run loop unblocks.
    assert stop.is_set()


async def test_watchdog_exits_when_get_scheduler_raises() -> None:
    """If the scheduler was never booted / was torn down, get_scheduler() raises —
    the watchdog must treat that as dead, not crash."""
    stop = asyncio.Event()
    with patch.object(worker, "get_scheduler", side_effect=RuntimeError("not initialised")):
        failed = await asyncio.wait_for(worker._watchdog(stop), timeout=2)
    assert failed is True
    assert stop.is_set()


async def test_watchdog_stamps_heartbeat_and_survives_while_running(monkeypatch) -> None:
    """While the scheduler is running the watchdog stamps the heartbeat each tick and
    keeps looping until told to stop (it does NOT report a failure)."""
    # Tight interval so the test is fast; the loop wakes promptly on stop anyway.
    monkeypatch.setattr(worker, "WATCHDOG_INTERVAL_SECONDS", 0.05)
    stamps = []
    monkeypatch.setattr(worker, "_stamp_heartbeat", lambda: stamps.append(1))

    stop = asyncio.Event()
    with patch.object(worker, "get_scheduler", return_value=SimpleNamespace(running=True)):
        task = asyncio.create_task(worker._watchdog(stop))
        await asyncio.sleep(0.16)        # let it tick a few times
        assert not task.done(), "watchdog must keep running while scheduler is alive"
        stop.set()                       # simulate SIGTERM
        failed = await asyncio.wait_for(task, timeout=2)

    assert failed is False, "a clean shutdown is not a failure exit"
    assert stamps, "heartbeat gauge should have been stamped at least once"


def test_stamp_heartbeat_never_raises() -> None:
    """The heartbeat stamp must be defensive — a metrics hiccup can't take the
    worker down (mirrors app.core.metrics' swallow-everything contract)."""
    # Whatever the prometheus import state, calling it is a no-op-safe operation.
    worker._stamp_heartbeat()


def test_run_returns_failure_flag_signature() -> None:
    """`run()` returns a bool so `main()` can map a dead-scheduler exit to sys.exit(1)."""
    import inspect

    src = inspect.getsource(worker.main)
    assert "sys.exit(1)" in src, "main() must exit non-zero on watchdog failure (F-24)"
