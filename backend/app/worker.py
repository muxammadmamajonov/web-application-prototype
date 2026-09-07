"""Dedicated scheduler worker process (#82).

APScheduler runs here, in its own Fly ``worker`` process, so the web (``app``)
machines don't each boot it. The Postgres lease lock in ``services/scheduler``
still guards against overlap during a deploy (old + new worker briefly
coexisting). Launched via the ``worker`` process command in fly.toml:
``python -m app.worker``.

(F-24) Liveness: per-job success gauges already catch a job that ERRORS, but
nothing detected the scheduler process going quietly dead *between* fire times —
a daily job's staleness alert wouldn't trip for ~24h, and if APScheduler stopped
internally (e.g. its jobstore connection died) the worker just sat in
``stop.wait()`` forever. This module now runs a short-interval watchdog that:
  * stamps ``muddato_scheduler_heartbeat_timestamp_seconds`` every tick while the
    scheduler is running (a fresh, fast-moving gauge — a dead scheduler is visible
    within ``WATCHDOG_INTERVAL_SECONDS``, not a day later), and
  * exits the process non-zero the moment ``get_scheduler().running`` is False, so
    the orchestrator (Fly) restarts it.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import time

from app.config import settings
from app.core.observability import (
    configure_logging,
    init_sentry,
    start_worker_metrics_server,
)
from app.services.notifications import close_bot as close_notification_bot
from app.services.scheduler import (
    get_scheduler,
    run_billing_cycle_job,
    setup_scheduler,
    shutdown_scheduler,
)

log = logging.getLogger("muddato.worker")

# How often the watchdog stamps the heartbeat and checks scheduler liveness.
# Short relative to the cron cadence (minutes/daily) so a silent death surfaces
# quickly, but not so tight it spins.
WATCHDOG_INTERVAL_SECONDS = 30

# (F-24) Heartbeat gauge — defensively optional, mirroring app.core.metrics: if
# prometheus_client isn't importable the gauge degrades to a no-op so a metrics
# hiccup can never take the worker down. Registered on the default registry, which
# ``start_worker_metrics_server`` exposes at /metrics for Fly to scrape.
try:
    from prometheus_client import Gauge

    _heartbeat = Gauge(
        "muddato_scheduler_heartbeat_timestamp_seconds",
        "Unix timestamp of the most recent scheduler liveness tick (worker "
        "watchdog). Stale ⇒ the scheduler process is dead or wedged.",
    )

    def _stamp_heartbeat() -> None:
        try:
            _heartbeat.set(time.time())
        except Exception:  # never let a metric break the watchdog
            log.debug("scheduler heartbeat stamp failed", exc_info=True)
except Exception:  # pragma: no cover - prometheus_client is a hard dep in practice
    def _stamp_heartbeat() -> None:
        return None


async def _boot() -> None:
    """Configure logging/Sentry and start the scheduler (unconditionally — this
    process *is* the scheduler, regardless of the RUN_SCHEDULER flag the web
    process honours)."""
    configure_logging()
    init_sentry()
    # Expose cron-health metrics (recorded in this process) so Fly can scrape them.
    start_worker_metrics_server(settings.metrics_port)
    await setup_scheduler()
    # Reconcile the current billing period on every worker boot. The operation
    # is DB-idempotent per (tenant, period), so this safely catches a missed
    # first-of-month fire after downtime without double-invoicing.
    await run_billing_cycle_job()


async def _watchdog(stop: asyncio.Event) -> bool:
    """(F-24) Heartbeat + liveness loop. Returns True iff it detected the scheduler
    dead (the caller then exits non-zero so the orchestrator restarts the process).

    Stamps the heartbeat gauge each tick while the scheduler is running; the instant
    ``get_scheduler().running`` is False (APScheduler stopped internally — jobstore
    death, internal crash) it logs and signals the run loop to stop with a failure."""
    while not stop.is_set():
        try:
            running = get_scheduler().running
        except Exception:
            # get_scheduler() raises if the scheduler was never booted / torn down.
            running = False
        if not running:
            log.error("worker.scheduler_dead — scheduler not running; exiting for restart")
            stop.set()
            return True
        _stamp_heartbeat()
        # Sleep in a way that wakes promptly on shutdown, so SIGTERM isn't delayed
        # by up to a full interval.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=WATCHDOG_INTERVAL_SECONDS)
    return False


async def run() -> bool:
    """Boot the scheduler and supervise it. Returns True on a failure exit (scheduler
    died) so ``main`` can propagate a non-zero status to the orchestrator."""
    await _boot()
    log.info("worker.started — scheduler running")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # non-Unix platforms
            loop.add_signal_handler(sig, stop.set)
    # Run the watchdog alongside the stop-wait; whichever resolves first ends the
    # process (a signal sets `stop`; a dead scheduler makes the watchdog set it too).
    failed = await _watchdog(stop)
    log.info("worker.stopping")
    try:
        await shutdown_scheduler()
    finally:
        await close_notification_bot()
        from app.core.redis import close_redis
        from app.db import engine

        await close_redis()
        await engine.dispose()
    return failed


def main() -> None:
    failed = asyncio.run(run())
    if failed:
        # Non-zero exit → Fly restarts the worker (F-24).
        sys.exit(1)


if __name__ == "__main__":
    main()
