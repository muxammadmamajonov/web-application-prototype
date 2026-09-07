"""Observability: domain (business) metrics — #70 follow-up.

The prometheus-fastapi-instrumentator already exports HTTP RED metrics. This
module adds the business signals HTTP metrics can't see — payment finalisations,
webhook RPC outcomes (Payme returns HTTP 200 for both a successful perform and an
auth failure), and cron health (jobs run in the worker, no HTTP). DB-free.
"""
from __future__ import annotations

import inspect


def test_metrics_module_exposes_record_helpers() -> None:
    from app.core import metrics

    for fn in ("record_payment", "record_webhook", "record_cron_run", "record_notification"):
        assert callable(getattr(metrics, fn)), fn


def test_record_helpers_are_safe_to_call() -> None:
    from app.core import metrics

    # Must never raise on the hot path — even called repeatedly with any labels.
    metrics.record_payment("payme", "approved")
    metrics.record_payment("click", "refunded")
    metrics.record_webhook("payme", "auth_failed")
    metrics.record_webhook("click", "ok")
    metrics.record_cron_run("send_reminders", "ok")
    metrics.record_cron_run("send_reminders", "error")
    metrics.record_notification("reminder.overdue", "sent")
    metrics.record_notification("reminder.due_today", "failed")


def test_notification_metric_counts_by_kind_and_result() -> None:
    # (MUDD-137) the "spike in notification failures" alert reads this counter
    # by result — a failed send must actually increment `result="failed"`,
    # not silently collapse into "sent".
    from prometheus_client import REGISTRY

    from app.core import metrics

    before_sent = REGISTRY.get_sample_value(
        "muddato_notification_deliveries_total", {"kind": "reminder.before_due", "result": "sent"},
    ) or 0
    before_failed = REGISTRY.get_sample_value(
        "muddato_notification_deliveries_total", {"kind": "reminder.before_due", "result": "failed"},
    ) or 0

    metrics.record_notification("reminder.before_due", "sent")
    metrics.record_notification("reminder.before_due", "failed")
    metrics.record_notification("reminder.before_due", "failed")

    after_sent = REGISTRY.get_sample_value(
        "muddato_notification_deliveries_total", {"kind": "reminder.before_due", "result": "sent"},
    )
    after_failed = REGISTRY.get_sample_value(
        "muddato_notification_deliveries_total", {"kind": "reminder.before_due", "result": "failed"},
    )
    assert after_sent == before_sent + 1
    assert after_failed == before_failed + 2


def test_domain_metrics_render_in_prometheus_exposition() -> None:
    from app.core import metrics

    metrics.record_payment("payme", "approved")
    metrics.record_webhook("payme", "ok")
    metrics.record_cron_run("send_reminders", "ok")
    metrics.record_notification("reminder.overdue", "sent")

    from prometheus_client import generate_latest

    out = generate_latest().decode()
    assert "muddato_payments_total" in out
    assert "muddato_webhook_requests_total" in out
    assert "muddato_cron_runs_total" in out
    assert "muddato_notification_deliveries_total" in out
    # a "last success" gauge backs cron-staleness alerts
    assert "muddato_cron_last_success_timestamp_seconds" in out


def test_cron_ok_updates_last_success_gauge() -> None:
    from app.core import metrics

    metrics.record_cron_run("apply_late_fees", "ok")
    from prometheus_client import REGISTRY

    val = REGISTRY.get_sample_value(
        "muddato_cron_last_success_timestamp_seconds", {"job": "apply_late_fees"}
    )
    assert val is not None and val > 0


def test_scheduler_records_cron_metrics() -> None:
    # Cron health is instrumented centrally (an APScheduler listener), so every
    # job is covered without touching each job body.
    import app.services.scheduler as sched

    src = inspect.getsource(sched)
    assert "record_cron_run" in src


def test_webhooks_record_outcome_metrics() -> None:
    import app.api.webhooks.payme as payme

    assert "record_webhook" in inspect.getsource(payme)


def test_worker_metrics_server_helper_is_safe() -> None:
    from app.core.observability import start_worker_metrics_server

    # port 0 → disabled: a no-op that never binds a socket and never raises.
    start_worker_metrics_server(0)


def test_worker_boots_a_metrics_server() -> None:
    # Cron metrics live in the worker process, so the worker must expose them or
    # Fly can't scrape cron health.
    import app.worker as worker

    assert "start_worker_metrics_server" in inspect.getsource(worker)
