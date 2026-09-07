"""(F-09) The cron lease must survive a long-running job: release is now a no-op and
the TTL is sized above worst-case runtime, so an overrunning job keeps its lock for
the whole run instead of losing it mid-flight and letting a second run start.

Pure-logic checks always run; the lock-survival check is DB-backed (self-skips
without TEST_DATABASE_URL via ``migrated_db``).
"""
from __future__ import annotations

import inspect
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


def test_release_is_a_noop() -> None:
    """``_release_job_lock`` must not mutate the lock — the TTL is the only release."""
    from app.services import scheduler

    src = inspect.getsource(scheduler._release_job_lock)
    # No UPDATE/DELETE against cron_locks in the release path anymore.
    assert "UPDATE cron_locks" not in src
    assert "DELETE" not in src.upper()
    assert "return None" in src


def test_ttl_exceeds_worst_case_runtime() -> None:
    """TTL must be well above any realistic job runtime (so a long job doesn't lose
    its lease) yet below the daily fire cadence."""
    from app.services import scheduler

    # >= 1h (comfortably above worst-case job runtime) and < 24h (the daily gap).
    assert scheduler._LOCK_TTL_SECONDS >= 60 * 60
    assert scheduler._LOCK_TTL_SECONDS < 24 * 60 * 60


async def test_lock_survives_a_completed_job(migrated_db, monkeypatch) -> None:
    """After a single_instance job runs to completion, its cron_locks row must still be
    held (locked_until in the future) — proving release no longer frees it early."""
    from app.services import scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    ran = {"n": 0}

    @scheduler.single_instance("f09_test_job")
    async def _job():
        ran["n"] += 1
        return {"ok": True}

    out = await _job()
    assert out == {"ok": True}
    assert ran["n"] == 1

    async with Session() as s:
        locked_until = (await s.execute(
            text("SELECT locked_until FROM cron_locks WHERE job_name = :n"),
            {"n": "f09_test_job"},
        )).scalar_one_or_none()

    assert locked_until is not None, "the lock row must exist after the job ran"
    # Release is a no-op → the lease is still in the future (not pulled back to now()).
    assert locked_until > datetime.now(UTC), (
        "lease must still be held after the job (release is a no-op; TTL is the backstop)"
    )


async def test_second_run_is_skipped_while_lock_held(migrated_db, monkeypatch) -> None:
    """With the lock held (release no-op), a second invocation before TTL expiry is
    skipped — the anti-overlap guarantee the lease exists for."""
    from app.services import scheduler

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)

    calls = {"n": 0}

    @scheduler.single_instance("f09_overlap_job")
    async def _job():
        calls["n"] += 1
        return {"ran": True}

    first = await _job()
    second = await _job()

    assert first == {"ran": True}
    assert second == {"skipped": "locked"}, "second run must be locked out by the held lease"
    assert calls["n"] == 1, "the job body must execute only once"
