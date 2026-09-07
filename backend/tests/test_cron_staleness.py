"""(#141) The cron-event listener must not let a single_instance lock-skip
reset the staleness gauge — otherwise a stalled lock-holder is masked because the
skipping instances keep recording "ok". A job's own no-op (holiday-skip) still
counts as a real run."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from apscheduler.events import EVENT_JOB_MISSED

from app.services.scheduler import _record_job_event


def _event(**kw) -> SimpleNamespace:
    kw.setdefault("job_id", "demo_job")
    kw.setdefault("exception", None)
    kw.setdefault("retval", None)
    return SimpleNamespace(**kw)


def _call(event) -> tuple:
    with patch("app.core.metrics.record_cron_run") as rec:
        _record_job_event(event)
    rec.assert_called_once()
    return rec.call_args.args


def test_lock_skip_recorded_as_skipped_not_ok() -> None:
    # {"skipped": "locked"} → "skipped" so it never stamps last-success.
    assert _call(_event(retval={"skipped": "locked"})) == ("demo_job", "skipped")


def test_holiday_noop_still_counts_as_ok() -> None:
    # A job that genuinely ran and no-op'd (holiday) is alive → "ok".
    assert _call(_event(retval={"skipped": True, "reason": "holiday"})) == ("demo_job", "ok")


def test_real_result_is_ok() -> None:
    assert _call(_event(retval={"applied": 3, "skipped_zero": 1})) == ("demo_job", "ok")
    assert _call(_event(retval=None)) == ("demo_job", "ok")


def test_exception_is_error() -> None:
    assert _call(_event(exception=RuntimeError("boom"))) == ("demo_job", "error")


def test_missed_fire_is_not_recorded_as_success() -> None:
    assert _call(_event(code=EVENT_JOB_MISSED)) == ("demo_job", "missed")
