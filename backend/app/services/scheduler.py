"""APScheduler — central background job registry for Muddato.

Why APScheduler over Celery
---------------------------
* Single-process, no separate worker daemon. Fly only runs one app machine
  per region; running Celery + Redis-backed broker would double our infra
  surface area for ~6 cron jobs.
* SQLAlchemy jobstore persists schedule state across machine restarts so
  a `fly deploy` doesn't drop the calendar.
* AsyncIOScheduler integrates cleanly with FastAPI's event loop — no thread
  hopping, no executor pools.

What lives here
---------------
* `setup_scheduler()` is called from FastAPI's lifespan startup; it boots
  the scheduler, registers every recurring job, and keeps a module-level
  reference so admin endpoints (Round 18+) can introspect/trigger jobs.
* Each job body is a thin orchestration function that opens a fresh DB
  session — APScheduler's executor doesn't propagate request-scoped state.
* All jobs are **idempotent**. They check status before acting so a missed
  fire + manual catch-up never double-applies a late fee or sends a
  reminder twice.

Schedule (all Asia/Tashkent)
----------------------------
* `03:00` — `mark_overdue_installments`
* `03:15` — `apply_late_fees`
* `09:00` — `send_reminders`
* `09:30` — `daily_owner_briefing` (Round 24 hook; defined here, body in
  reports service)

Tests cover:
  * idempotent overdue marking (no double-status-flip on re-run)
  * late fee math (one Payment row per day, not per run)
  * reminder de-dup (no second SMS to same customer same offset same day)
"""
from __future__ import annotations

import asyncio
import functools
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.business_time import (
    tashkent_day_bounds_utc,
    tashkent_day_start_utc,
    tashkent_month_start_utc,
    tashkent_today,
)
from app.core.currency import money_by_currency, tenant_primary_currency
from app.core.documents import SUPPORTED_LOCALES, normalize_locale
from app.core.money_utils import round_som
from app.core.rls_manifest import ROLE_WORKER

# (Batch 5) Scheduled work is cross-tenant by nature, so it runs under the
# explicit WORKER authority rather than inheriting whatever the ambient
# context happens to be. `worker_session` restores the previous context on
# exit, so a job cannot leave its authority behind for a request handler
# sharing the process (RUN_SCHEDULER is on in the web process too).
from app.db import SessionLocal
from app.db import worker_session as _worker_session
from app.models import (
    Customer,
    CustomerRiskSignal,
    Holiday,
    Installment,
    Payment,
    TenantSettings,
)
from app.models.enums import (
    InstallmentStatus,
    PaymentMethod,
    PaymentStatus,
    RiskSignalSource,
    RiskSignalStatus,
)
from app.services.document_retention import sweep_archived_documents_job
from app.services.installments import recompute_state

log = logging.getLogger("muddato.scheduler")


def worker_session():
    """A WORKER-authority session on THIS module's ``SessionLocal``.

    The indirection keeps the long-standing test seam: the late-fee and cron
    tests swap ``scheduler.SessionLocal`` for a factory bound to the test
    engine, and reading the module global here (rather than closing over the
    import) is what lets that keep working now that jobs run under an explicit
    authority.
    """
    return _worker_session(factory=SessionLocal)

# Module-level singleton so admin endpoints can introspect / pause / trigger.
_scheduler: AsyncIOScheduler | None = None

TASHKENT_TZ = "Asia/Tashkent"

# Cron jobs run in-process on every app machine. To stop a job firing twice
# when >1 machine is up (deploy overlap / autoscale), each job claims a
# TTL'd row in ``cron_locks`` before running. The claim is a single atomic
# upsert (pooler-safe) and self-expires so a crashed instance can't wedge it.
#
# (F-09) The TTL is the lease lifetime measured from job START and must exceed the
# worst-case runtime of the longest job (reminders / briefing / PII-purge over a
# large tenant base with per-row network calls). Previously a 30-min TTL was taken
# at start and the lock was FREED immediately in ``finally`` — so a job running
# past 30 min lost its lease and the next fire (or a second worker during a deploy)
# re-acquired and ran concurrently, double-sending SMS. We now (a) never release
# early — release is a no-op (see ``_release_job_lock``), so an overrunning job
# keeps its lock for the whole TTL — and (b) size the TTL well above any realistic
# job runtime while staying far below the daily fire cadence. The crashed-instance
# backstop is unchanged: the upsert's ``WHERE locked_until < now()`` reclaims the
# row once the TTL lapses, so a dead worker self-heals.
_LOCK_TTL_SECONDS = 6 * 60 * 60  # 6h: » worst-case job runtime, « the 24h daily gap

# Cron jobs that touch per-row data page through it in keyset batches of this
# size (ordered by id) so memory stays bounded regardless of table size.
_CRON_BATCH = 500


async def _iter_keyset(db, base_stmt, pk_col, *, batch: int = _CRON_BATCH):
    """Yield rows of ``base_stmt`` in keyset batches ordered by ``pk_col`` so a
    job never loads the whole result set into memory at once. The yielded
    entity must expose ``.id`` (all our cron targets do)."""
    last = None
    while True:
        stmt = base_stmt
        if last is not None:
            stmt = stmt.where(pk_col > last)
        rows = (await db.execute(stmt.order_by(pk_col.asc()).limit(batch))).scalars().all()
        if not rows:
            return
        for r in rows:
            yield r
        if len(rows) < batch:
            return
        last = rows[-1].id


async def _try_acquire_job_lock(name: str, ttl_seconds: int = _LOCK_TTL_SECONDS) -> bool:
    async with worker_session() as db:
        row = (await db.execute(
            text(
                """
                INSERT INTO cron_locks (job_name, locked_until)
                VALUES (:n, now() + make_interval(secs => :ttl))
                ON CONFLICT (job_name) DO UPDATE
                    SET locked_until = excluded.locked_until
                    WHERE cron_locks.locked_until < now()
                RETURNING job_name
                """
            ),
            {"n": name, "ttl": ttl_seconds},
        )).first()
        await db.commit()
        return row is not None


async def _release_job_lock(name: str) -> None:
    """(F-09) Intentionally a no-op — the lease is released by TTL expiry only.

    Previously this pulled ``locked_until`` back to ``now()`` the instant a job
    finished. Combined with a fixed 30-min TTL taken at job start and never
    renewed, a job that overran its TTL had ALREADY lost the lease (the row had
    expired), so the next fire — or a second worker mid-deploy — re-acquired and
    ran concurrently, double-sending SMS. Heartbeating the lease from inside every
    long job would work but threads a periodic renew (and a fresh session) through
    each job body — more surface area, and a silently-failed renew re-opens the
    race. The lower-risk fix is to NOT release early and size the TTL above
    worst-case runtime (see ``_LOCK_TTL_SECONDS``): an overrunning job keeps its
    lock for the whole run, and the upsert's ``WHERE locked_until < now()`` still
    reclaims the row after the TTL so a crashed instance self-heals. Kept as a
    function (not removed) so the ``single_instance`` ``finally`` contract and any
    callers/tests stay intact."""
    return None


def single_instance(name: str):
    """Decorator: run the job only if this instance wins the cron lock."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if not await _try_acquire_job_lock(name):
                log.info("cron.%s skipped — lock held by another instance", name)
                return {"skipped": "locked"}
            try:
                return await fn(*args, **kwargs)
            finally:
                await _release_job_lock(name)
        return wrapper
    return deco


# ── boot ───────────────────────────────────────────────────────────────────

def get_scheduler() -> AsyncIOScheduler:
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialised — call setup_scheduler() first")
    return _scheduler


def _record_job_event(event: Any) -> None:
    """APScheduler listener → cron-health metrics. Centralised here so every
    registered job is covered without instrumenting each job body; a stalled job
    surfaces as a stale ``muddato_cron_last_success_timestamp_seconds`` gauge."""
    from app.core.metrics import record_cron_run
    job_id = getattr(event, "job_id", "unknown")
    if getattr(event, "code", None) == EVENT_JOB_MISSED:
        record_cron_run(job_id, "missed")
        log.error("cron.job_missed job=%s", job_id)
        return
    if event.exception:
        record_cron_run(job_id, "error")
        # Metric first, so a logging fault can't cost us the counter. Error CLASS
        # only: a job's exception message can carry row/tenant detail from the
        # failing query.
        log.error("cron.job_failed job=%s err=%s", job_id, type(event.exception).__name__)
        return
    # (#141) A single_instance lock-skip returns {"skipped": "locked"} with no
    # exception. Recording it as "ok" would stamp the last-success gauge and mask a
    # stalled lock-holder (the skipping instances all look healthy). Record it as
    # "skipped" so the staleness gauge only advances on a REAL run. A job's own
    # no-op (e.g. the holiday-skip {"skipped": True}) still counts as "ok" — that
    # instance genuinely executed.
    retval = getattr(event, "retval", None)
    if isinstance(retval, dict) and retval.get("skipped") == "locked":
        record_cron_run(job_id, "skipped")
        return
    record_cron_run(job_id, "ok")


def make_jobstore_engine() -> Any:
    """A sync engine for APScheduler's jobstore, pinned to the worker authority.

    (Batch 5 correction) APScheduler is a third-party library holding its own
    connections; it never passes through ``Session.after_begin``, so it never
    issues ``SET LOCAL ROLE``. It used to be handed ``DATABASE_URL_SYNC`` when
    that URL was the schema owner, which made the jobstore a standing
    owner-privileged connection pool sitting beside the application.

    It now uses the same restricted application login as everything else. That
    login is NOINHERIT and holds no privilege on ``apscheduler_jobs``, so the
    authority has to be assumed on every connection this engine opens. No
    separate credential is introduced — the authority is narrowed, the identity
    is not widened.

    HOW the role is assumed is load-bearing, and the first attempt was wrong.
    It was pinned with the libpq ``options='-c role=...'`` startup parameter.
    That works on a direct connection and is SILENTLY DISCARDED by a connection
    pooler: production talks to Supabase's Supavisor, which does not forward
    unrecognised startup parameters and does not error when it drops them. The
    connection therefore came up as the restricted login, ``apscheduler_jobs``
    had just been revoked from that login by ``rf0failclosed``, and the
    scheduler died at boot with ``permission denied for table
    apscheduler_jobs`` — while ``/health``, ``/ready`` and the synthetic
    monitor stayed green, because they only ever exercise the ``app`` process.

    ``SET ROLE`` is an ordinary statement, so it survives any pooler. It is
    issued once per new DBAPI connection rather than per transaction because
    APScheduler runs its statements outside an explicit transaction, where
    ``SET LOCAL`` would be a no-op with a warning. The engine is dedicated to
    the jobstore, so a session-lifetime role is exactly the intended scope.

    ``apscheduler_jobs`` is created by migration ``e5f6``, so the jobstore's
    ``checkfirst`` create is always a no-op and needs no CREATE privilege.
    """
    from sqlalchemy import create_engine, event

    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)

    if settings.rls_enabled:

        @event.listens_for(engine, "connect")
        def _assume_worker_authority(dbapi_conn: Any, _record: Any) -> None:
            # Not parameterised: a role name cannot be bound, and ROLE_WORKER is
            # a module constant, never request data.
            cur = dbapi_conn.cursor()
            try:
                cur.execute(f"SET ROLE {ROLE_WORKER}")
            finally:
                cur.close()

    return engine


#: Grace window for the once-a-day business jobs, overriding the 30-minute
#: scheduler default.
#:
#: 30 minutes is a deploy-length window. It is not a *worker-down* window, and
#: on Fly a stopped worker machine is not started by `fly deploy` — so the
#: realistic outage here is hours, not minutes. With the default, a worker that
#: came back at 09:31 dropped that day's reminders on the floor silently: the
#: fire was "missed", nothing was logged as an error, and every customer due
#: that day simply was not told.
#:
#: Six hours is safe because every one of these jobs is idempotent — reminders
#: dedup on a Notification row per (tenant, kind, installment, day), overdue
#: marking and late fees are keyed off dates, the purge is a delete-what-is-old
#: sweep. `coalesce=True` (above) still collapses a backlog of missed fires into
#: exactly one run, so a two-day outage sends one day's reminders, not two.
_DAILY_MISFIRE_GRACE = 6 * 60 * 60


async def setup_scheduler() -> AsyncIOScheduler:
    """Boot APScheduler with a Postgres jobstore + register recurring jobs.

    Called once from FastAPI's lifespan. Safe to call again if it already
    booted (the registration loop replaces jobs by stable id).
    """
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    jobstore = SQLAlchemyJobStore(engine=make_jobstore_engine(), tablename="apscheduler_jobs")
    sched = AsyncIOScheduler(
        jobstores={"default": jobstore},
        timezone=TASHKENT_TZ,
        # coalesce missed runs (e.g. after a long deploy) into a single fire,
        # don't try to "catch up" five 03:00 cron runs in a row.
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30 * 60},
        # NOTE: the 30-minute default above is right for the frequent jobs
        # (heartbeat), and WRONG for the once-a-day ones — see
        # `_DAILY_MISFIRE_GRACE`.
    )

    # Every 5 minutes: retire review actions whose payment is decided. Catches
    # work lost to a restart and leases abandoned by a dead process, so a
    # Telegram outage delays retirement rather than ending it.
    from app.services.payment_review_notify import sweep_review_notices_job

    sched.add_job(
        sweep_review_notices_job,
        IntervalTrigger(minutes=5),
        id="sweep_review_notices",
        replace_existing=True,
    )
    # Dead-man's switch (see app/services/heartbeat.py). Deliberately NOT wrapped
    # in `single_instance`: with more than one machine we want ANY healthy one to
    # hold the switch open. A lock would let a single wedged lock-holder trip the
    # alarm while the fleet is fine — a false page — and, worse, could let a
    # lock-holder that dies mid-TTL keep the switch open while nothing pings.
    from app.services.heartbeat import emit_heartbeat_job

    sched.add_job(
        emit_heartbeat_job,
        IntervalTrigger(seconds=settings.heartbeat_interval_seconds),
        id="emit_heartbeat",
        replace_existing=True,
        # A missed ping is absorbed by the external grace period; catching up on
        # a backlog of them tells the switch "alive" for a window we were not.
        coalesce=True,
        max_instances=1,
    )
    # (Batch 6) Retention for archived Telegram documents. Daily and off-peak:
    # the work is a cache sweep, never user-visible, and every deletion it makes
    # is of a row no lookup can reach. Idempotent, so a missed run costs nothing
    # but a day of backlog.
    sched.add_job(
        sweep_archived_documents_job,
        CronTrigger(hour=4, minute=30, timezone=TASHKENT_TZ),
        id="sweep_archived_documents",
        replace_existing=True,
        misfire_grace_time=_DAILY_MISFIRE_GRACE,
    )
    sched.add_job(
        mark_overdue_installments_job,
        CronTrigger(hour=3, minute=0, timezone=TASHKENT_TZ),
        id="mark_overdue_installments",
        replace_existing=True,
        misfire_grace_time=_DAILY_MISFIRE_GRACE,
    )
    sched.add_job(
        apply_late_fees_job,
        CronTrigger(hour=3, minute=15, timezone=TASHKENT_TZ),
        id="apply_late_fees",
        replace_existing=True,
        misfire_grace_time=_DAILY_MISFIRE_GRACE,
    )
    sched.add_job(
        publish_risk_signals_job,
        CronTrigger(hour=3, minute=30, timezone=TASHKENT_TZ),
        id="publish_risk_signals",
        replace_existing=True,
        misfire_grace_time=_DAILY_MISFIRE_GRACE,
    )
    sched.add_job(
        send_reminders_job,
        CronTrigger(hour=9, minute=0, timezone=TASHKENT_TZ),
        id="send_reminders",
        replace_existing=True,
        misfire_grace_time=_DAILY_MISFIRE_GRACE,
    )
    # Round 24 — daily owner briefing. Hooked here so the schedule lives
    # alongside the rest; body in reports.service.
    sched.add_job(
        daily_owner_briefing_job,
        CronTrigger(hour=9, minute=30, timezone=TASHKENT_TZ),
        id="daily_owner_briefing",
        replace_existing=True,
        misfire_grace_time=_DAILY_MISFIRE_GRACE,
    )
    # Round 26 — trial lifecycle enforcement. Flips demo→suspended at trial
    # end, suspended→blocked after a 14-day grace, and warns owners 3/1/0
    # days before expiry.
    sched.add_job(
        enforce_trials_job,
        CronTrigger(hour=3, minute=45, timezone=TASHKENT_TZ),
        id="enforce_trials",
        replace_existing=True,
        misfire_grace_time=_DAILY_MISFIRE_GRACE,
    )
    # Round 27 — subscription billing. Issue invoices on the 1st of each
    # month, then run the dunning ladder daily (overdue → suspend → block).
    sched.add_job(
        run_billing_cycle_job,
        CronTrigger(day=1, hour=4, minute=0, timezone=TASHKENT_TZ),
        id="billing_cycle",
        replace_existing=True,
        misfire_grace_time=_DAILY_MISFIRE_GRACE,
    )
    sched.add_job(
        run_dunning_job,
        CronTrigger(hour=4, minute=15, timezone=TASHKENT_TZ),
        id="billing_dunning",
        replace_existing=True,
        misfire_grace_time=_DAILY_MISFIRE_GRACE,
    )
    # (#166) Data-retention purge — delete PII past its retention window (old SMS /
    # notifications / broadcasts / audit, dead invitations, aged risk signals).
    sched.add_job(
        purge_expired_pii_job,
        # 04:45, not 04:30 (#MUDD-111 cron audit). `sweep_archived_documents`
        # already holds 04:30, and both are batched DELETE sweeps over the same
        # database — `max_instances=1` is per-job and does nothing to stop two
        # different jobs contending. Fifteen minutes apart, on a worker that is
        # otherwise idle at that hour, they no longer overlap.
        CronTrigger(hour=4, minute=45, timezone=TASHKENT_TZ),
        id="purge_expired_pii",
        replace_existing=True,
        misfire_grace_time=_DAILY_MISFIRE_GRACE,
    )

    sched.add_listener(
        _record_job_event,
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )
    sched.start()
    log.info("scheduler.started jobs=%s", [j.id for j in sched.get_jobs()])
    _scheduler = sched
    return sched


async def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler.stopped")
        _scheduler = None


# ── helpers ────────────────────────────────────────────────────────────────

async def is_business_day(db: AsyncSession, d: date) -> bool:
    """True iff `d` isn't an official UZ day-off."""
    row = (await db.execute(
        select(Holiday).where(
            Holiday.holiday_date == d,
            Holiday.is_official_day_off.is_(True),
        )
    )).scalar_one_or_none()
    return row is None


# ── jobs ───────────────────────────────────────────────────────────────────

@single_instance("mark_overdue_installments")
async def mark_overdue_installments_job() -> dict[str, Any]:
    """Cron 03:00 — flip `active` → `overdue` for installments whose
    `next_due_date` is in the past and `paid_amount < principal`.

    Idempotent: only acts on `active` rows that satisfy the date test, so
    re-running the same day is a no-op.
    """
    today = tashkent_today()
    async with worker_session() as db:
        # Set-based UPDATE — no per-row loading, scales to any table size.
        result = await db.execute(
            update(Installment)
            .where(
                Installment.status == InstallmentStatus.active,
                Installment.next_due_date.is_not(None),
                Installment.next_due_date < today,
                Installment.paid_amount < Installment.principal,
            )
            .values(status=InstallmentStatus.overdue)
        )
        await db.commit()
        flipped = result.rowcount or 0  # type: ignore[attr-defined]  # rowcount lives on CursorResult (UPDATE), not the base Result stub
        log.info("cron.mark_overdue flipped=%d", flipped)
        return {"flipped": flipped}


@single_instance("publish_risk_signals")
async def publish_risk_signals_job() -> dict[str, Any]:
    """Cron 03:30 — manage the cross-tenant risk network (Round 23).

    Three idempotent passes:
      1. **Promote** any ``pending_appeal`` auto signal whose 14-day appeal
         window has closed → ``published``.
      2. **Withdraw** auto signals whose source installment is no longer
         overdue (paid off / cancelled) — the network shouldn't punish a
         customer who has since cured the debt.
      3. **Create** a ``pending_appeal`` signal for every installment that is
         60+ days overdue, but only for tenants that opted into the network.

    Only opted-in tenants contribute; the matching is by salted JSHSHIR hash.
    """
    from app.services import risk as risk_svc

    today = tashkent_today()
    async with worker_session() as db:
        # 1) promote expired appeal windows
        promoted = 0
        pending = (await db.execute(
            select(CustomerRiskSignal).where(
                CustomerRiskSignal.status == RiskSignalStatus.pending_appeal,
                CustomerRiskSignal.appeal_deadline.is_not(None),
                CustomerRiskSignal.appeal_deadline <= today,
            )
        )).scalars().all()
        for sig in pending:
            sig.status = RiskSignalStatus.published
            promoted += 1

        # 2) withdraw auto signals whose installment cured
        withdrawn = 0
        active_auto = (await db.execute(
            select(CustomerRiskSignal, Installment)
            .join(Installment, Installment.id == CustomerRiskSignal.source_installment_id)
            .where(
                CustomerRiskSignal.source == RiskSignalSource.auto,
                CustomerRiskSignal.status != RiskSignalStatus.withdrawn,
            )
        )).all()
        for sig, inst in active_auto:
            if inst.status != InstallmentStatus.overdue:
                sig.status = RiskSignalStatus.withdrawn
                withdrawn += 1

        # 3) create new signals for 60+ day overdue (opted-in tenants only)
        opted_in_ids = set((await db.execute(
            select(TenantSettings.tenant_id).where(
                TenantSettings.risk_network_opt_in.is_(True)
            )
        )).scalars().all())

        created = 0
        if opted_in_ids:
            _overdue_base = (
                select(Installment, Customer)
                .join(Customer, Customer.id == Installment.customer_id)
                .where(
                    Installment.status == InstallmentStatus.overdue,
                    Installment.tenant_id.in_(opted_in_ids),
                    Installment.next_due_date.is_not(None),
                )
            )
            _last = None
            while True:  # keyset batches so memory stays bounded at scale
                _stmt = _overdue_base
                if _last is not None:
                    _stmt = _stmt.where(Installment.id > _last)
                _rows = (await db.execute(
                    _stmt.order_by(Installment.id.asc()).limit(_CRON_BATCH)
                )).all()
                if not _rows:
                    break
                for inst, cust in _rows:
                    days = (today - inst.next_due_date).days
                    severity = risk_svc.severity_for_days_overdue(days)
                    if severity is None or days < risk_svc.AUTO_PUBLISH_OVERDUE_DAYS:
                        continue
                    remaining = inst.remaining_balance
                    new_sig = await risk_svc.upsert_signal(
                        db,
                        tenant_id=inst.tenant_id,
                        customer=cust,
                        severity=severity,
                        source=RiskSignalSource.auto,
                        status=RiskSignalStatus.pending_appeal,
                        installment_id=inst.id,
                        appeal_deadline=today + timedelta(days=risk_svc.APPEAL_WINDOW_DAYS),
                        evidence={"days_overdue": days, "remaining": str(remaining)},
                    )
                    if new_sig is not None:
                        created += 1
                _last = _rows[-1][0].id
                if len(_rows) < _CRON_BATCH:
                    break

        await db.commit()
        log.info(
            "cron.publish_risk promoted=%d withdrawn=%d created=%d opted_in=%d",
            promoted, withdrawn, created, len(opted_in_ids),
        )
        return {
            "promoted": promoted, "withdrawn": withdrawn,
            "created": created, "opted_in_tenants": len(opted_in_ids),
        }


def _late_fee_days(*, today: date, last_fee_date: date | None, next_due_date: date | None) -> int:
    """Number of days a late fee should cover today.

    The gap since the LAST fee was charged — so a daily run accrues exactly one day at a
    time, and a missed cron day is caught up by the larger gap — or since the due date for
    the very first fee (a one-time catch-up). At least 1. (#863: charging
    ``today - next_due_date`` = total days overdue on every daily run compounded the fee
    quadratically.)
    """
    anchor = last_fee_date or next_due_date
    if anchor is None:
        return 1
    return max(1, (today - anchor).days)


async def apply_late_fees_job() -> dict[str, Any]:
    """MVP gate for the dormant late-fee engine."""
    if not settings.enable_late_fees:
        log.info("cron.apply_late_fees skipped — feature disabled")
        return {"skipped": True, "reason": "feature_disabled"}
    return await _apply_late_fees_job_enabled()


@single_instance("apply_late_fees")
async def _apply_late_fees_job_enabled() -> dict[str, Any]:
    """Cron 03:15 — accrue a late fee for each overdue installment that the
    tenant has configured a non-zero `late_fee_pct_per_day` for.

    Math: `late_fee_pct_per_day * days_overdue_since_last_fee * remaining_principal`,
    rounded via `round_som` to match the contract ledger's whole-so'm granularity
    (MUDD-65 M-01) — `round_amount`'s 2-decimal rounding would leave a fractional
    residual `principal` can never actually reach.

    (MUDD-65 H4) The fee is added straight onto `principal` — the ONLY thing
    `Installment.remaining_balance` (`principal - paid_amount`) and every
    `total_outstanding`/`remaining` read site derives from — so it is
    immediately part of the customer's owed balance, not just a `Payment` row
    nobody's debt reflects. It must NOT touch `paid_amount`: that would make an
    unpaid fee look already paid. A `late_fee` Payment row is still recorded
    for the audit trail; `api/me.py`, `api/platform/overview.py` and
    `services/reports.py` already exclude `method == late_fee` from revenue/GMV
    (`# accrual, not a payment`), so recording it there does not double-count.

    Idempotency strategy: at most one late-fee Payment per installment per
    day. The query filters out installments that already got a fee today.
    """
    today = tashkent_today()
    applied = 0
    skipped_zero = 0
    skipped_dup = 0
    skipped_paid = 0  # (F-05) cured/paid-off under the lock between snapshot and insert
    last_id = None
    async with worker_session() as db:
      while True:
        q = select(Installment).where(
            Installment.status == InstallmentStatus.overdue,
            Installment.next_due_date.is_not(None),
            Installment.paid_amount < Installment.principal,
        )
        if last_id is not None:
            q = q.where(Installment.id > last_id)
        installments = (await db.execute(
            q.order_by(Installment.id.asc()).limit(_CRON_BATCH)
        )).scalars().all()
        if not installments:
            break
        # (#142) Batch-load tenant default late-fee rates for this page instead of a
        # per-installment SELECT — was an N+1 across the overdue set.
        _rate_ids = {inst.tenant_id for inst in installments}
        _default_rate = {
            tid: rate
            for tid, rate in (await db.execute(
                select(TenantSettings.tenant_id, TenantSettings.default_late_fee_pct_per_day)
                .where(TenantSettings.tenant_id.in_(_rate_ids))
            )).all()
        }
        for inst in installments:
            # Effective rate: per-installment override OR tenant default. (Read off
            # the unlocked snapshot — the rate doesn't move under us; it's the
            # balance/status we must re-read under the lock below.)
            rate = inst.late_fee_pct_per_day
            if rate is None:
                rate = _default_rate.get(inst.tenant_id) or Decimal("0")
            if rate is None or rate <= 0:
                skipped_zero += 1
                continue

            try:
                # (F-05) Lock the installment and RE-CHECK it's still overdue & unpaid
                # *under the lock* before computing/inserting the fee. The candidate
                # set above is an UNLOCKED snapshot; every payment path locks the
                # installment row, so without this a concurrent webhook can apply the
                # final payment and flip the loan to completed between our SELECT and
                # our INSERT — charging a late fee on a just-paid-off loan. The savepoint
                # also catches a same-day dup on the unique index (uq_payments_late_fee_day).
                async with db.begin_nested():
                    # ``populate_existing()`` is essential: ``inst`` is already in this
                    # session's identity map from the unlocked snapshot above, so a plain
                    # ``SELECT … FOR UPDATE`` would take the DB lock but return the STALE
                    # cached object (with expire_on_commit=False its attributes aren't
                    # refreshed). populate_existing forces the locked row's current values
                    # to overwrite the in-session attributes, so the re-check below sees
                    # the post-payment state, not the snapshot.
                    locked = (await db.execute(
                        select(Installment)
                        .where(Installment.id == inst.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )).scalar_one_or_none()
                    # Still overdue and still actually unpaid? If a payment cured it
                    # while we waited for the lock, charge nothing.
                    if (
                        locked is None
                        or locked.status != InstallmentStatus.overdue
                        or locked.paid_amount >= locked.principal
                    ):
                        skipped_paid += 1
                        continue

                    # The last day a late fee was charged drives "days since last fee" so
                    # a daily run accrues one day at a time (#863). If that's today it's a
                    # dup — skip (the partial unique index is the hard guard).
                    last_fee_date = (await db.execute(
                        select(func.max(Payment.fee_date)).where(
                            Payment.installment_id == locked.id,
                            Payment.method == PaymentMethod.late_fee,
                        )
                    )).scalar_one_or_none()
                    if last_fee_date == today:
                        skipped_dup += 1
                        continue

                    days_overdue = _late_fee_days(
                        today=today, last_fee_date=last_fee_date,
                        next_due_date=locked.next_due_date,
                    )
                    remaining = locked.remaining_balance  # computed from the LOCKED row
                    fee = round_som(rate * Decimal(days_overdue) * remaining)  # (MUDD-65 M-01)
                    if fee <= 0:
                        continue

                    db.add(Payment(
                        tenant_id=locked.tenant_id,
                        installment_id=locked.id,
                        customer_id=locked.customer_id,
                        # The contract's currency, not the shop's current
                        # setting: a late fee belongs to the loan it accrued on.
                        currency=locked.currency,
                        amount=fee,
                        method=PaymentMethod.late_fee,
                        status=PaymentStatus.approved,
                        fee_date=today,
                        notes=f"late_fee {days_overdue}d × {rate} × {remaining}",
                        paid_at=datetime.now(UTC),
                        approved_at=datetime.now(UTC),
                    ))
                    # (MUDD-65 H4) Grow the actual debt, not just the audit trail —
                    # see the docstring above. paid_amount is untouched, so this can
                    # only move the loan further from "settled", never closer;
                    # recompute_state is still called for consistency (it derives
                    # next_due_date/status from paid vs. monthly, both unchanged here).
                    locked.principal = (locked.principal or Decimal("0")) + fee
                    recompute_state(locked, now=datetime.now(UTC))
                applied += 1
            except IntegrityError:
                skipped_dup += 1
        # Commit this batch and advance the keyset cursor.
        await db.commit()
        last_id = installments[-1].id
        if len(installments) < _CRON_BATCH:
            break
    log.info(
        "cron.apply_late_fees applied=%d skipped_zero=%d skipped_dup=%d skipped_paid=%d",
        applied, skipped_zero, skipped_dup, skipped_paid,
    )
    return {
        "applied": applied,
        "skipped_zero": skipped_zero,
        "skipped_dup": skipped_dup,
        "skipped_paid": skipped_paid,
    }


@single_instance("send_reminders")
async def send_reminders_job() -> dict[str, Any]:
    """Cron 09:00 — send due-date reminders to customers based on each
    tenant's `reminder_offsets_days` (default `[3, 1, 0]` = 3 days before,
    1 day before, day-of).

    Skips runs on official UZ holidays so customers aren't pinged on
    Independence Day / Eid mornings.

    Idempotency: tracked via Notification rows — we don't re-send for the
    same (customer, installment, offset_day) tuple.
    """
    from app.models import Notification
    from app.services import audit
    from app.services.notifications import (
        MiniAppButton,
        installment_pay_deeplink,
        notify_tenant_target,
        reminder_context,
        render,
        tenant_installment_deeplink,
    )

    today = tashkent_today()
    # (MUDD-136 Req 5) Whole-job cap on EXTRA retry attempts — mutated in place
    # by `_notify_recipient_with_backoff` as the loop below spends it. Bounds a
    # fleet-wide Telegram outage to a fixed amount of extra time for the WHOLE
    # run (every due installment of every tenant), not per-recipient: without
    # this, a real outage would turn a bounded per-recipient retry into an
    # unbounded one simply by having enough recipients, holding the job's 6h
    # lease for the rest of the day.
    _retry_budget = {"remaining": _REMINDER_JOB_RETRY_BUDGET}

    async with worker_session() as db:
        if not await is_business_day(db, today):
            log.info("cron.send_reminders skip=holiday date=%s", today)
            return {"skipped": True, "reason": "holiday"}

        # Pull active + overdue installments due in the next N days, in keyset
        # batches so memory stays bounded regardless of table size.
        max_offset = 7  # outer envelope; per-tenant filter narrows below
        cutoff = today + timedelta(days=max_offset)
        _reminders_base = select(Installment).where(
            Installment.status.in_([InstallmentStatus.active, InstallmentStatus.overdue]),
            Installment.next_due_date.is_not(None),
            Installment.next_due_date <= cutoff,
        )

        sent_customer = 0
        sent_owner = 0
        sent_sms = 0
        skipped = 0
        checked = 0
        # A customer with no linked Telegram account AND no successful SMS
        # fallback leaves NO row behind: `notify_user` (which is what writes a
        # Notification) is only ever called when a `recipient` User exists, so
        # this case previously vanished with zero trace — not a failed delivery
        # an admin could find, just nothing. Counted and logged so "N customers
        # got no reminder today via any channel" is visible instead of silent.
        unreachable = 0
        # (#75) Cache TenantSettings per tenant for the run — every reminder for
        # the same tenant shares one settings row, so a per-installment lookup is
        # a needless N+1. Bounded by tenant count, not installment count.
        _ts_cache: dict[UUID, TenantSettings | None] = {}

        async def _settings_for(tenant_id: UUID) -> TenantSettings | None:
            if tenant_id not in _ts_cache:
                _ts_cache[tenant_id] = (await db.execute(
                    select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)
                )).scalar_one_or_none()
            return _ts_cache[tenant_id]

        async for inst in _iter_keyset(db, _reminders_base, Installment.id):
            checked += 1
            if inst.next_due_date is None:
                continue
            offset = (inst.next_due_date - today).days  # negative = overdue
            # Determine which template applies.
            s = await _settings_for(inst.tenant_id)
            offsets = list((s.reminder_offsets_days if s else None) or [3, 1, 0])
            overdue_every = (s.reminder_overdue_every_days if s else 3) or 3
            templates = (s.reminder_templates if s else {}) or {}

            # Pick template key
            if offset > 0:
                if offset not in offsets:
                    continue
                tpl_key = "before_due"
            elif offset == 0:
                if 0 not in offsets:
                    continue
                tpl_key = "due_today"
            else:
                # overdue: send every Nth day after due
                days_overdue = -offset
                if days_overdue % overdue_every != 0:
                    continue
                tpl_key = "overdue"

            # Dedup: same tenant + kind + installment, already sent today (#75) —
            # an indexed lookup on (tenant_id, kind, installment_id, created_at)
            # instead of a leading-wildcard LIKE on the body that can't use an index.
            already = (await db.execute(
                select(Notification.id).where(
                    Notification.tenant_id == inst.tenant_id,
                    Notification.kind == f"reminder.{tpl_key}",
                    Notification.installment_id == inst.id,
                    Notification.delivered_at.is_not(None),
                    # (MUDD-136 second pass) `datetime.combine(today, min, tzinfo=UTC)`
                    # mislabels the TASHKENT-dated `today` as if it were already a
                    # UTC midnight — 5 hours late. In the 19:00-24:00 UTC window (when
                    # Tashkent has rolled to tomorrow but the UTC calendar day hasn't),
                    # that threshold lands in the FUTURE relative to the Notification
                    # row this same run just committed, so `already` finds nothing and
                    # a same-day re-run double-sends — breaking the exact idempotency
                    # invariant this cron's docstring promises. `_try_sms_fallback`
                    # right below already gets this correct via
                    # `tashkent_day_start_utc()`; the customer-facing dedup here did
                    # not. Caught live: this test suite failed under the real wall
                    # clock at 19:09 UTC while verifying MUDD-136's second pass.
                    Notification.created_at >= tashkent_day_start_utc(),
                ).limit(1)
            )).scalar_one_or_none()
            if already:
                skipped += 1
                continue

            customer = (await db.execute(
                select(Customer).where(Customer.id == inst.customer_id)
            )).scalar_one_or_none()
            if customer is None:
                continue

            # Resolve "destination user" — invitation flow links customer →
            # user via telegram_id — BEFORE rendering, so the template can be
            # picked in the customer's own locale rather than always the
            # server's Uzbek default. A missing/garbage locale folds to "uz"
            # (`normalize_locale`), never raises.
            from app.models import User
            recipient = None
            if customer.telegram_id:
                recipient = (await db.execute(
                    select(User).where(User.telegram_id == customer.telegram_id)
                )).scalar_one_or_none()
            recipient_locale = normalize_locale(recipient.locale if recipient else None)

            remaining = inst.remaining_balance
            # ONE builder for cron, preview and the manual per-installment send
            # (#MUDD-111). It also formats the money, so `4800000.00` cannot
            # reach a customer, and it supplies `days_until_due` / `phone`,
            # which the default `before_due` and `owner_overdue_alert`
            # templates reference and this dict used to omit.
            ctx = reminder_context(
                customer_name=customer.full_name or "",
                currency=inst.currency,
                amount=inst.monthly_amount or 0,
                due_date=inst.next_due_date.isoformat(),
                contract_number=inst.contract_number or "",
                remaining=remaining,
                days_overdue=max(0, -offset),
                days_until_due=max(0, offset),
                phone=customer.phone or "",
            )

            # A tenant-authored override (`templates.get`) is a single string
            # per key, not locale-branched — it wins regardless of the
            # customer's locale. Only the SHIPPED default picks uz/ru; making
            # per-tenant custom copy locale-aware needs a schema + Settings-UI
            # change, tracked separately (see the MUDD-136 closing comment).
            tpl = (
                templates.get(tpl_key)
                or _default_template(tpl_key, recipient_locale)
            )
            body = render(tpl, **ctx)

            telegram_ok = False
            if recipient is not None:
                # Bot-first: the "Open" button launches the Mini App straight
                # into THIS installment's pay screen (one tap reminder → pay).
                # (MUDD-136 Req 5) Bounded retry lives INSIDE this call — the
                # same-day dedup check above (`already`) has already run once
                # per installment; the retry never re-enters it, it only
                # repeats the send for the target that check already cleared.
                telegram_ok = await _notify_recipient_with_backoff(
                    db,
                    user_id=recipient.id,
                    tenant_id=inst.tenant_id,
                    kind=f"reminder.{tpl_key}",
                    title=_default_title(recipient_locale),
                    installment_id=inst.id,
                    body=body,
                    link=installment_pay_deeplink(inst.id),
                    retry_budget=_retry_budget,
                )
                if telegram_ok:
                    sent_customer += 1

            # SMS fallback (Round 25): no Telegram link OR the Telegram send
            # failed → try SMS, subject to tenant budget + customer opt-in.
            sms_ok = False
            if not telegram_ok:
                sms_ok = await _try_sms_fallback(db, s, customer, inst, tpl_key, body)
                if sms_ok:
                    sent_sms += 1

            if not telegram_ok and not sms_ok:
                # (MUDD-136 Req 7) Two distinct cases land here, both now on
                # the record: a customer with no channel at all (no linked
                # Telegram, no usable SMS fallback) — previously left NO row
                # anywhere — and a customer WHO HAS a channel but every send
                # to it permanently failed (bounded retries exhausted, no SMS
                # fallback either). The second case previously vanished just
                # as completely: `recipient is not None` skipped the old
                # `unreachable` counter entirely, even though nothing else
                # recorded it either (a failed-but-persisted Notification row
                # exists, but nothing surfaces it to an operator as "today's
                # reminder never reached this customer").
                reason = "no_channel" if recipient is None else "all_channels_failed"
                unreachable += 1
                log.warning(
                    "cron.send_reminders no_channel_reached tenant=%s customer=%s "
                    "installment=%s kind=%s reason=%s",
                    inst.tenant_id, customer.id, inst.id, tpl_key, reason,
                )
                # IDs only in `after` — never a phone number, never the
                # message body. `GET /platform/audit` is the named operator
                # surface (requirement 7): a log line on a Fly worker that
                # scales to zero is a diagnostic, not something an admin can
                # search a week later.
                await audit.record(
                    db,
                    action="reminder.delivery_failed",
                    tenant_id=inst.tenant_id,
                    entity_type="installment",
                    entity_id=str(inst.id),
                    after={
                        "customer_id": str(customer.id),
                        "kind": tpl_key,
                        "reason": reason,
                    },
                )
                await db.commit()

            # For overdue, also poke the tenant's owner so they can take action.
            if tpl_key == "overdue":
                owner_tpl = templates.get("owner_overdue_alert") or _default_template("owner_overdue_alert")
                count = await notify_tenant_target(
                    db,
                    tenant_id=inst.tenant_id,
                    kind="reminder.owner_overdue",
                    title="Kechikkan to'lov",
                    body=render(owner_tpl, **ctx),
                    # The customer's reminder has carried a one-tap action since
                    # the start; the owner's copy of the same event did not, so
                    # the person who actually chases the debt had to find the
                    # contract by hand. Same intent, both encodings — a shop
                    # whose reminder target is a channel cannot take a `web_app`
                    # button, and one would make Telegram reject the message.
                    mini_app=MiniAppButton(
                        label="Shartnomani ochish",
                        mini_app_url=(
                            f"{settings.frontend_url.rstrip('/')}"
                            f"/uz/tenant/installments?installment={inst.id}"
                        ),
                        deep_link=tenant_installment_deeplink(inst.id),
                    ),
                )
                sent_owner += count

        log.info(
            "cron.send_reminders sent_customer=%d sent_sms=%d sent_owner=%d "
            "skipped_dup=%d unreachable=%d total=%d",
            sent_customer, sent_sms, sent_owner, skipped, unreachable, checked,
        )
        return {
            "sent_customer": sent_customer,
            "sent_sms": sent_sms,
            "sent_owner": sent_owner,
            "skipped_dup": skipped,
            "unreachable": unreachable,
            "checked": checked,
        }


# (MUDD-136 Req 5) A transient Telegram failure (timeout, 5xx) used to cost
# the customer a full day: `send_to_telegram` already retries ONCE on a
# flood-control wait, but nothing retried a plain timeout or per-recipient
# 5xx — the very next attempt was tomorrow's cron run, and only then after
# falling through to SMS (or `unreachable`) today. Two independent caps,
# because the blast radius of "retry on failure" in a loop over every due
# installment of every tenant is bigger than the hole it closes:
#   - per-recipient: at most one retry (two attempts total) — a customer who
#     is unreachable stays unreachable quickly, not after a long stall.
#   - per-job: `_REMINDER_JOB_RETRY_BUDGET` extra attempts total across the
#     WHOLE run — a fleet-wide Telegram outage must not turn a bounded
#     per-recipient retry into an unbounded one just by having enough
#     recipients, and must not make the 09:00 job hold its 6h lease all day.
_REMINDER_TELEGRAM_MAX_ATTEMPTS = 2
_REMINDER_JOB_RETRY_BUDGET = 200


async def _notify_recipient_with_backoff(
    db: AsyncSession,
    *,
    user_id: UUID,
    tenant_id: UUID,
    kind: str,
    title: str,
    installment_id: UUID,
    body: str,
    link: str,
    retry_budget: dict[str, int],
) -> bool:
    """Send one reminder with a small bounded retry, INSIDE the per-installment
    dedup guard the caller already passed.

    The caller's same-day ``Notification`` dedup check (``already``, above in
    ``send_reminders_job``) runs exactly once per installment, before this is
    ever called. This function only repeats the SEND for the one recipient
    that check already cleared — it never re-scans installments or re-runs
    the dedup query, so it cannot manufacture a second delivery slot the way
    a retry wrapped AROUND the whole per-installment loop could.
    ``notify_user(..., retry_existing=True)`` reuses the same ``Notification``
    row across attempts (bumping ``attempt_count``), so even a retried send
    stays exactly one row for (tenant, kind, installment, day) — the
    invariant the whole cron's idempotency rests on.

    Reuses ``payment_review_notify._backoff``'s bounded-exponential-with-
    jitter SHAPE at a much smaller scale (that module's own defaults — 30s
    base, 3600s cap — are tuned for a decoupled 5-minute sweep job, not an
    inline retry inside a fan-out over every due installment).
    A failed send is retried only while the Telegram circuit breaker (#255)
    is still closed. In a fleet-wide outage the breaker is OPEN, so
    ``send_to_telegram`` returns ``False`` before touching the network — a
    retry there is a pure sleep, and worse, the shared ``retry_budget`` is
    spent first-come, so blocked-bot/breaker-open recipients early in the
    fan-out can exhaust it before a genuinely transient timeout later in the
    run gets its one retry. Checking ``allow()`` here costs nothing (the
    breaker itself already tracks this) and turns an unbounded amount of
    sleep-with-zero-API-calls into an immediate return.
    """
    from app.core.breaker import telegram_breaker
    from app.services.notifications import notify_user
    from app.services.payment_review_notify import _backoff

    attempt = 0
    while True:
        attempt += 1
        ok = await notify_user(
            db,
            user_id=user_id,
            tenant_id=tenant_id,
            kind=kind,
            title=title,
            installment_id=installment_id,
            body=body,
            link=link,
            retry_existing=True,
        )
        if (
            ok
            or attempt >= _REMINDER_TELEGRAM_MAX_ATTEMPTS
            or retry_budget["remaining"] <= 0
            or not telegram_breaker.allow()
        ):
            return ok
        retry_budget["remaining"] -= 1
        delay = _backoff(attempt, base_seconds=1, cap_seconds=5).total_seconds()
        await asyncio.sleep(delay)


async def _try_sms_fallback(
    db: AsyncSession,
    s: TenantSettings | None,
    customer: Customer,
    inst: Installment,
    tpl_key: str,
    text: str,
) -> bool:
    """Send a reminder via SMS when Telegram is unavailable.

    Gated by: provider configured, tenant has a positive monthly budget with
    headroom, customer phone is a valid UZ mobile, and the customer hasn't
    explicitly opted out of SMS. Idempotent per (installment, kind, day): the slot
    is RESERVED in ``sms_messages`` (a partial unique index) *before* the provider
    call, so even two overlapping cron runs can't double-send (F-09). Returns True
    only on a successful provider send.
    """
    from app.models import NotificationChannel, SmsMessage
    from app.models.enums import NotificationChannelType, SmsStatus
    from app.services import sms_eskiz

    if not sms_eskiz.is_configured():
        return False
    budget = int((s.sms_budget_uzs_per_month if s else 0) or 0)
    if budget <= 0:
        return False

    phone = sms_eskiz.normalize_uz_phone(customer.phone)
    if phone is None:
        return False

    # Respect explicit opt-out: an `sms` channel row whose opted_in_at is NULL
    # means "recorded a preference but not opted in" → don't send. No row at
    # all → default-allow as a fallback.
    sms_chan = (await db.execute(
        select(NotificationChannel).where(
            NotificationChannel.customer_id == customer.id,
            NotificationChannel.channel == NotificationChannelType.sms,
        )
    )).scalar_one_or_none()
    if sms_chan is not None and sms_chan.opted_in_at is None:
        return False

    kind = f"reminder.{tpl_key}"
    today = tashkent_today()
    today_start = tashkent_day_start_utc()
    month_start = tashkent_month_start_utc()

    # Dedup fast-path: already sent (or pending) for this installment + kind today.
    # The partial unique index uq_sms_reminder_dedup is the HARD guard (below); this
    # is just a cheap early-out that avoids the reserve attempt in the common case.
    dup = (await db.execute(
        select(SmsMessage).where(
            SmsMessage.tenant_id == inst.tenant_id,
            SmsMessage.installment_id == inst.id,
            SmsMessage.kind == kind,
            SmsMessage.created_at >= today_start,
            SmsMessage.status.in_([SmsStatus.sent, SmsStatus.pending]),
        )
    )).scalar_one_or_none()
    if dup is not None:
        return False

    # Budget headroom (month-to-date successful spend + this message).
    spent = (await db.execute(
        select(func.coalesce(func.sum(SmsMessage.cost_uzs), Decimal("0"))).where(
            SmsMessage.tenant_id == inst.tenant_id,
            SmsMessage.status == SmsStatus.sent,
            SmsMessage.created_at >= month_start,
        )
    )).scalar_one()
    est = sms_eskiz.estimate_cost_uzs(sms_eskiz.strip_html(text))
    if Decimal(spent) + Decimal(est) > Decimal(budget):
        # (#106) Per-row — debug, not info, so it doesn't flood logs at scale.
        log.debug("cron.sms_fallback skip=over_budget tenant=%s", inst.tenant_id)
        return False

    # (F-09) RESERVE today's dedup slot BEFORE calling the provider, so the unique
    # index prevents a *duplicate send* (real cost), not merely a duplicate log row.
    # Two overlapping runs (a lapsed lease — see F-09) both pass the read-then-write
    # `dup` check above; the loser's INSERT here collides on uq_sms_reminder_dedup and
    # we treat that IntegrityError as "already (being) sent" → skip the provider call.
    # The row is reserved `pending` (which the partial index covers); we finalise its
    # status after the send. A `failed` send flips it out of the index, so a genuine
    # retry later the same day is still allowed.
    msg = SmsMessage(
        tenant_id=inst.tenant_id,
        customer_id=customer.id,
        installment_id=inst.id,
        kind=kind,
        phone=phone,
        text=sms_eskiz.strip_html(text)[:1000],
        parts=1,
        cost_uzs=Decimal("0"),
        status=SmsStatus.pending,
        dedup_day=today,
    )
    try:
        async with db.begin_nested():  # savepoint: a dup hits the unique index
            db.add(msg)
    except IntegrityError:
        # Another (overlapping) run already claimed today's slot for this
        # installment+kind — don't send a second SMS.
        return False

    sender = (s.sms_sender_alpha if s else None) or None
    result = await sms_eskiz.send_sms(customer.phone, text, sender=sender)

    # Finalise the reserved row with the real provider outcome.
    msg.parts = result.parts
    msg.cost_uzs = Decimal(result.cost_uzs)
    msg.status = SmsStatus.sent if result.ok else SmsStatus.failed
    msg.provider_message_id = result.provider_message_id
    msg.error = result.error
    await db.commit()
    return result.ok


def _briefing_money(totals: dict[str, Decimal], primary: str) -> str:
    """Render a money figure as every currency it is actually held in.

    Single-currency shops — which is every shop until it writes its first
    contract in a second one — read exactly what they read before: one amount
    with one label, the tenant's own. A mixed shop reads both, joined, because
    Muddato has no exchange rate to fold them with and inventing one would turn
    a visible wrong number into an invisible one (see `app.core.currency`).

    ``primary`` leads the line and labels an empty total, so "nothing collected"
    is denominated in the shop's own currency rather than in whichever code
    happened to sort first.
    """
    from app.core.documents import money_with_currency

    if not totals:
        return money_with_currency(0, primary, "uz")
    ordered = sorted(totals.items(), key=lambda kv: (kv[0] != primary, kv[0]))
    return " + ".join(money_with_currency(total, code, "uz") for code, total in ordered)


@single_instance("daily_owner_briefing")
async def daily_owner_briefing_job() -> dict[str, Any]:
    """Cron 09:30 — Round 24 hook. Sends each owner a single Telegram
    summary: "Bugun: 12 to'lov · 3 kechikkan · 1.2M so'm".

    Body lives here for now; Round 24 will move it to `services/reports.py`
    and add per-tenant customisation.
    """
    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole
    from app.services.notifications import send_to_telegram

    now = datetime.now(UTC)
    today = tashkent_today(now)
    async with worker_session() as db:
        if not await is_business_day(db, today):
            return {"skipped": True, "reason": "holiday"}

        from app.models import Payment
        from app.models.enums import PaymentStatus
        # (MUDD-138) "Kecha" is a Tashkent BUSINESS day, so it is a half-open
        # [start, end) range over a UTC timestamp column — not
        # `func.date(ts) == yesterday`, which asks the database's calendar and
        # files every 00:00-05:00 Tashkent payment under the wrong day.
        yesterday = today - timedelta(days=1)
        yesterday_start, yesterday_end = tashkent_day_bounds_utc(yesterday)

        sent = 0
        # (F-19) Keyset-batch tenants (like the other crons) instead of loading the
        # whole platform tenant list into memory, and compute the per-tenant figures
        # with SQL COUNT/SUM aggregates rather than materialising every due-today and
        # every overdue installment into Python lists just to len()/sum() them.
        async for t in _iter_keyset(db, select(Tenant), Tenant.id):
            # Today's count + due-today monthly amounts, GROUPED BY the contract's
            # own currency. A shop holding both UZS and USD contracts is an ordinary
            # state (a contract keeps the currency it was written in, whatever the
            # shop's setting says today), so an ungrouped SUM here adds unlike units
            # and prints a total that is true of no currency at all (MUDD-114).
            due_rows = (await db.execute(
                select(
                    Installment.currency,
                    func.count(Installment.id),
                    func.coalesce(func.sum(Installment.monthly_amount), Decimal("0")),
                ).where(
                    Installment.tenant_id == t.id,
                    Installment.next_due_date == today,
                    Installment.status.in_([InstallmentStatus.active, InstallmentStatus.overdue]),
                ).group_by(Installment.currency)
            )).all()
            # The COUNT is a headcount of contracts, so it sums across currencies
            # exactly as before; only the MONEY is kept apart.
            due_today_count = sum(int(row[1]) for row in due_rows)
            due_by_currency = money_by_currency([(row[0], row[2]) for row in due_rows])

            overdue_count = (await db.execute(
                select(func.count(Installment.id)).where(
                    Installment.tenant_id == t.id,
                    Installment.status == InstallmentStatus.overdue,
                )
            )).scalar_one()
            # Yesterday's collected (approved) payments, per currency.
            paid_ts = func.coalesce(Payment.paid_at, Payment.approved_at, Payment.created_at)
            collected_by_currency = money_by_currency((await db.execute(
                select(
                    Payment.currency,
                    func.coalesce(func.sum(Payment.amount), Decimal("0")),
                ).where(
                    Payment.tenant_id == t.id,
                    Payment.status == PaymentStatus.approved,
                    Payment.method != PaymentMethod.late_fee,  # (#121) accrual, not collected
                    paid_ts >= yesterday_start,
                    paid_ts < yesterday_end,
                ).group_by(Payment.currency)
            )).all())

            if not due_today_count and not overdue_count and not collected_by_currency:
                continue
            # Only a shop that actually gets a message pays for this lookup. It
            # decides which currency LEADS the line and what an empty figure is
            # labelled with — never what an existing contract is worth.
            primary = await tenant_primary_currency(db, t.id)
            text = (
                f"<b>Bugungi briefing</b>\n"
                f"📅 Bugun to'lash kerak: <b>{due_today_count}</b> "
                f"(~{_briefing_money(due_by_currency, primary)})\n"
                f"⚠️ Kechikkanlar: <b>{overdue_count}</b>\n"
                f"💰 Kecha yig'ildi: <b>{_briefing_money(collected_by_currency, primary)}</b>"
            )
            owners = (await db.execute(
                select(User.telegram_id)
                .join(TenantUser, TenantUser.user_id == User.id)
                .where(
                    TenantUser.tenant_id == t.id,
                    TenantUser.role == TenantRole.owner,
                    TenantUser.is_active.is_(True),
                )
            )).all()
            for (tg_id,) in owners:
                if tg_id and await send_to_telegram(tg_id, text):
                    sent += 1
        log.info("cron.daily_owner_briefing sent=%d", sent)
        return {"sent": sent}


# Days of suspension before a tenant is fully blocked (no access).
SUSPEND_GRACE_DAYS = 14
# When to warn owners that the trial is ending (days remaining).
TRIAL_WARN_OFFSETS = (3, 1, 0)


@single_instance("enforce_trials")
async def enforce_trials_job() -> dict[str, Any]:
    """Cron 03:45 — drive the trial → suspended → blocked lifecycle.

    Three idempotent passes (status is the guard, so re-running is safe):
      1. **Expire**: ``demo`` tenants whose ``trial_ends_at`` has passed →
         ``suspended`` (stamp ``suspended_at``). Suspended = read-only.
      2. **Block**: ``suspended`` tenants whose ``suspended_at`` + 14d has
         passed → ``blocked`` (no access).
      3. **Warn**: owners of ``demo`` tenants 3/1/0 days before expiry get a
         single Telegram nudge per offset per day.
    """
    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole, TenantStatus
    from app.services.notifications import (
        MiniAppButton,
        billing_deeplink,
        send_to_telegram,
    )

    now = datetime.now(UTC)
    today = tashkent_today(now)
    today_start = tashkent_day_start_utc(now)

    async with worker_session() as db:
        # The platform owner picks what an expired demo does: suspend (default,
        # read-only), block (locked immediately) or keep (leave it running).
        from app.services.demo_plan import get_config
        on_expiry = str((await get_config(db)).get("on_expiry") or "suspend")

        # 1) demo → suspended/blocked (trial elapsed). "keep" skips this step.
        expired = (await db.execute(
            select(Tenant).where(
                Tenant.status == TenantStatus.demo,
                Tenant.trial_ends_at.is_not(None),
                Tenant.trial_ends_at < now,
            )
        )).scalars().all()
        suspended = 0
        if on_expiry != "keep":
            for t in expired:
                t.status = TenantStatus.blocked if on_expiry == "block" else TenantStatus.suspended
                t.suspended_at = now
                suspended += 1

        # 2) suspended → blocked (grace elapsed)
        grace_cutoff = now - timedelta(days=SUSPEND_GRACE_DAYS)
        stale = (await db.execute(
            select(Tenant).where(
                Tenant.status == TenantStatus.suspended,
                Tenant.suspended_at.is_not(None),
                Tenant.suspended_at < grace_cutoff,
            )
        )).scalars().all()
        blocked = 0
        for t in stale:
            t.status = TenantStatus.blocked
            blocked += 1

        await db.commit()

        # 3) warn owners of trials nearing expiry (3/1/0 days out)
        from app.models import Notification
        warned = 0
        active_trials = (await db.execute(
            select(Tenant).where(
                Tenant.status == TenantStatus.demo,
                Tenant.trial_ends_at.is_not(None),
            )
        )).scalars().all()
        for t in active_trials:
            assert t.trial_ends_at is not None  # filtered by trial_ends_at.is_not(None) above
            days_left = (t.trial_ends_at.date() - today).days
            if days_left not in TRIAL_WARN_OFFSETS:
                continue
            kind = f"trial.warn_{days_left}"
            # Dedup: one warning per tenant per offset per day.
            already = (await db.execute(
                select(Notification).where(
                    Notification.tenant_id == t.id,
                    Notification.kind == kind,
                    Notification.created_at >= today_start,
                )
            )).scalar_one_or_none()
            if already is not None:
                continue

            if days_left == 0:
                text = (
                    "<b>Sinov muddati bugun tugaydi</b>\n"
                    "Hisobingiz vaqtincha to'xtatiladi. Tarifni tanlab, "
                    "ishni davom ettiring."
                )
            else:
                text = (
                    f"<b>Sinov muddati {days_left} kundan keyin tugaydi</b>\n"
                    "Uzluksiz ishlash uchun tarifni tanlang."
                )
            owners = (await db.execute(
                select(User)
                .join(TenantUser, TenantUser.user_id == User.id)
                .where(
                    TenantUser.tenant_id == t.id,
                    TenantUser.role == TenantRole.owner,
                    TenantUser.is_active.is_(True),
                )
            )).scalars().all()
            # The owner's explicit ask (#MUDD-111 §5): a warning that the shop
            # is about to stop working must carry the action that prevents it,
            # not just the news. `BillingPage` stays routable even for a blocked
            # tenant precisely so this button is never a dead link.
            pay_button = MiniAppButton(
                label="💳 Tarifni tanlash",
                mini_app_url=f"{settings.frontend_url.rstrip('/')}/uz/tenant/billing",
                deep_link=billing_deeplink(),
            )
            delivered = False
            for owner in owners:
                if owner.telegram_id and await send_to_telegram(
                    owner.telegram_id, text, mini_app=pay_button,
                ):
                    delivered = True
                # Stamp a Notification (per owner — user_id is required) so the
                # dedup guard fires next run regardless of Telegram delivery.
                db.add(Notification(
                    user_id=owner.id,
                    tenant_id=t.id,
                    kind=kind,
                    title="Sinov muddati",
                    body=text,
                ))
            if delivered:
                warned += 1
        await db.commit()

        log.info(
            "cron.enforce_trials suspended=%d blocked=%d warned=%d",
            suspended, blocked, warned,
        )
        return {"suspended": suspended, "blocked": blocked, "warned": warned}


# ── default templates (seed if tenant didn't override) ─────────────────────

_DEFAULTS_UZ = {
    "before_due":
        "Salom, {customer_name}!\n"
        "Sizning {contract_number} shartnomangiz bo'yicha keyingi to'lov:\n"
        "💰 <b>{amount} {currency}</b>\n"
        "📅 {due_date}\n\n"
        "O'z vaqtida to'lasangiz, kechiktirish penyasiz qoladi.",
    "due_today":
        "Salom, {customer_name}!\n"
        "Bugun to'lash kerak: <b>{amount} {currency}</b>\n"
        "Shartnoma: {contract_number}\n\n"
        "Mini-App orqali tezda to'lashingiz mumkin.",
    "overdue":
        "Salom, {customer_name}.\n"
        "Sizning to'lovingiz <b>{days_overdue} kun</b> kechikdi.\n"
        "Qarz: <b>{remaining} {currency}</b>\n\n"
        "Iltimos, eng tezda to'lab qo'ying.",
    "owner_overdue_alert":
        "⚠️ <b>Kechikkan mijoz</b>\n"
        "{customer_name} — {days_overdue} kun kechikdi\n"
        "Qarz: {remaining} {currency}\n"
        "Shartnoma: {contract_number}",
}

# (MUDD-136) Russian counterpart of the shipped defaults, keyed identically.
# `User.locale` (already resolved for every reminder recipient) picks between
# the two — see `_default_template`. Only the SHIPPED defaults are
# locale-aware: a tenant's OWN custom `reminder_templates` copy is a single
# string per key (not `{uz: ..., ru: ...}`), so a tenant that has customized
# copy still always sees their own text regardless of the customer's locale.
# Branching that too needs a schema + Settings-UI change (frontend, not owned
# by this lane) — flagged on the issue rather than done here.
_DEFAULTS_RU = {
    "before_due":
        "Здравствуйте, {customer_name}!\n"
        "Ближайший платёж по договору {contract_number}:\n"
        "💰 <b>{amount} {currency}</b>\n"
        "📅 {due_date}\n\n"
        "Оплатите вовремя — без пени за просрочку.",
    "due_today":
        "Здравствуйте, {customer_name}!\n"
        "Сегодня нужно оплатить: <b>{amount} {currency}</b>\n"
        "Договор: {contract_number}\n\n"
        "Оплатить можно быстро через Mini-App.",
    "overdue":
        "Здравствуйте, {customer_name}.\n"
        "Ваш платёж просрочен на <b>{days_overdue} дн.</b>\n"
        "Долг: <b>{remaining} {currency}</b>\n\n"
        "Пожалуйста, оплатите как можно скорее.",
    # No "owner_overdue_alert" here (LOW-5): unlike the three customer-facing
    # keys above, this one is never rendered through `_default_template(key,
    # locale)` with a real recipient locale — `notify_tenant_target` fans out
    # to every active owner of a tenant (plus possibly a channel), and a
    # multi-recipient broadcast has no single `User.locale` to pick. The RU
    # copy that used to sit here could never be reached by any call site;
    # keeping it was coverage that didn't exist. `_CUSTOMER_TEMPLATE_KEYS`
    # below is what the UZ/RU parity guard actually checks.
}

# The three keys a REAL customer locale ever picks between (see the comment on
# `_DEFAULTS_RU` above for why "owner_overdue_alert" is deliberately excluded).
# Asserted at import time, not just in a test: this file failing to import in
# production would be caught immediately (Fly's release_command runs Alembic
# then boots the app — a broken import here fails loudly, not silently) if UZ
# ever grows a customer key RU doesn't have.
_CUSTOMER_TEMPLATE_KEYS = frozenset({"before_due", "due_today", "overdue"})
assert set(_DEFAULTS_UZ) >= _CUSTOMER_TEMPLATE_KEYS, _DEFAULTS_UZ.keys()
assert set(_DEFAULTS_RU) == _CUSTOMER_TEMPLATE_KEYS, (
    f"UZ/RU customer-facing template keys diverged: "
    f"uz-only={_CUSTOMER_TEMPLATE_KEYS - set(_DEFAULTS_RU)!r}, "
    f"ru-only={set(_DEFAULTS_RU) - _CUSTOMER_TEMPLATE_KEYS!r}"
)

_TEMPLATES_BY_LOCALE = {"uz": _DEFAULTS_UZ, "ru": _DEFAULTS_RU}

# (MUDD-136) The customer-facing reminder title was a hardcoded UZ literal
# even after the body itself became locale-aware — a RU recipient got a
# bilingual message (UZ title + RU body). Keyed like the body defaults so a
# locale that has no title falls back to "uz", never to a missing key.
_TITLES_BY_LOCALE = {"uz": "To'lov eslatmasi", "ru": "Напоминание об оплате"}

# (MUDD-136 second pass, reviewer LOW) `_TITLES_BY_LOCALE` and
# `_TEMPLATES_BY_LOCALE` are independent dicts; nothing previously stopped
# them from covering different locale sets. Today both happen to cover all of
# `SUPPORTED_LOCALES` — the day a third locale is added to one and not the
# other, a recipient in it gets a title in one language and a body in
# another (HIGH-1's exact shape), silently, because both dicts fall back to
# "uz" independently rather than failing. Asserted at import time for the
# same reason as the customer-template-keys check above.
assert set(_TITLES_BY_LOCALE) == set(_TEMPLATES_BY_LOCALE) == set(SUPPORTED_LOCALES), (
    _TITLES_BY_LOCALE.keys(), _TEMPLATES_BY_LOCALE.keys(), SUPPORTED_LOCALES,
)


def _default_title(locale: str = "uz") -> str:
    return _TITLES_BY_LOCALE.get(normalize_locale(locale), _TITLES_BY_LOCALE["uz"])


@single_instance("billing_cycle")
async def run_billing_cycle_job() -> dict[str, Any]:
    """Cron (1st of month, 04:00) — issue this period's subscription invoices."""
    from app.services import billing

    async with worker_session() as db:
        issued = await billing.run_billing_cycle(db)
        await db.commit()
    log.info("cron.billing_cycle issued=%d", issued)
    return {"issued": issued}


@single_instance("billing_dunning")
async def run_dunning_job() -> dict[str, Any]:
    """Cron (daily 04:15) — flip overdue invoices and suspend/block debtors."""
    from app.services import billing

    async with worker_session() as db:
        summary = await billing.run_dunning(db)
        await db.commit()
        # After the commit, deliberately: the status change is the authoritative
        # outcome and must not depend on Telegram being reachable. `transitions`
        # is popped so the returned summary stays JSON-shaped for the job log.
        transitions = summary.pop("transitions", [])
        summary["notified"] = await billing.notify_dunning_transitions(db, transitions)
    log.info("cron.billing_dunning %s", summary)
    return summary


def _default_template(key: str, locale: str = "uz") -> str:
    """The shipped default for ``key``, in ``locale`` (folds to "uz" if unknown).

    Callers that don't pass a locale (the owner-alert template, the admin
    preview endpoints) keep today's UZ-only behaviour — this is additive.
    """
    return _TEMPLATES_BY_LOCALE.get(normalize_locale(locale), _DEFAULTS_UZ).get(key, "")


# Useful for the admin endpoint that lists the canonical defaults, and for the
# Settings UI's "Reset to default" action.
def default_reminder_templates(locale: str = "uz") -> dict[str, str]:
    return dict(_TEMPLATES_BY_LOCALE.get(normalize_locale(locale), _DEFAULTS_UZ))


# ── #166 data-retention purge ─────────────────────────────────────────────────

# Conservative, fixed retention windows. PII past these is deleted so logs /
# notifications / expired tokens / aged risk-signals don't grow without bound (the
# Privacy Policy promises deletion). Financial records (installments/payments) are
# NEVER touched — those are kept by the deliberate no-soft-delete design (#100).
_SMS_RETENTION_DAYS = 180
_NOTIFICATION_RETENTION_DAYS = 90
_BROADCAST_RETENTION_DAYS = 180
_AUDIT_RETENTION_DAYS = 365
_INVITATION_RETENTION_DAYS = 30   # days PAST expiry before a dead invite is purged


async def _purge_batched(db: AsyncSession, model: Any, predicate: Any, *, batch: int = 1000) -> int:
    """Delete rows matching ``predicate`` in id-keyed batches (bounded locks, one
    commit per batch). Idempotent. Returns the number of rows deleted."""
    total = 0
    while True:
        ids = (await db.execute(
            select(model.id).where(predicate).limit(batch)
        )).scalars().all()
        if not ids:
            break
        await db.execute(delete(model).where(model.id.in_(ids)))
        await db.commit()
        total += len(ids)
    return total


async def _purge_expired(db: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """The retention-purge body (parameterized by session + clock for testing).
    Deletes PII past its retention window; never touches installments/payments."""
    from app.models import (
        AuditLog,
        Broadcast,
        CustomerRiskSignal,
        Invitation,
        Notification,
        SmsMessage,
    )

    now = now or datetime.now(UTC)
    counts: dict[str, int] = {}
    counts["sms_messages"] = await _purge_batched(
        db, SmsMessage, SmsMessage.created_at < now - timedelta(days=_SMS_RETENTION_DAYS),
    )
    counts["notifications"] = await _purge_batched(
        db, Notification, Notification.created_at < now - timedelta(days=_NOTIFICATION_RETENTION_DAYS),
    )
    # Deleting the parent broadcast cascades broadcast_recipients (telegram_id PII).
    counts["broadcasts"] = await _purge_batched(
        db, Broadcast, Broadcast.created_at < now - timedelta(days=_BROADCAST_RETENTION_DAYS),
    )
    counts["audit_log"] = await _purge_batched(
        db, AuditLog, AuditLog.created_at < now - timedelta(days=_AUDIT_RETENTION_DAYS),
    )
    # Long-dead invitations hold preset PII; purge once well past expiry.
    counts["invitations"] = await _purge_batched(
        db, Invitation, Invitation.expires_at < now - timedelta(days=_INVITATION_RETENTION_DAYS),
    )
    # Risk signals carry a natural expiry; drop the ones that have aged out.
    counts["risk_signals"] = await _purge_batched(
        db, CustomerRiskSignal, CustomerRiskSignal.expires_at < now,
    )
    return counts


@single_instance("purge_expired_pii")
async def purge_expired_pii_job() -> dict[str, Any]:
    """Cron 04:30 — delete PII past its retention window so it doesn't accumulate
    forever (#166). Financial records are never deleted."""
    async with worker_session() as db:
        counts = await _purge_expired(db)
    log.info("cron.purge_expired_pii %s", counts)
    return {"purged": counts}
