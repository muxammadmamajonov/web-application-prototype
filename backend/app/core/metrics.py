"""Domain (business) metrics for Prometheus — observability follow-up to #70.

The prometheus-fastapi-instrumentator already exports HTTP RED metrics
(``http_requests_total`` / ``http_request_duration_seconds`` by handler + status)
on the default registry, so anything registered here is scraped at /metrics too.
This module adds the business signals HTTP metrics can't see:

  * ``muddato_payments_total{method,status}`` — payment finalisations.
  * ``muddato_webhook_requests_total{provider,result}`` — payment-provider webhook
    RPC outcomes. Payme/Click return HTTP 200 for *both* a successful perform and
    an auth failure (the result is in the JSON-RPC body), so HTTP status can't
    distinguish them — a spike in ``result="auth_failed"`` is an attack signal.
  * ``muddato_cron_runs_total{job,result}`` + ``muddato_cron_last_success_
    timestamp_seconds{job}`` — cron health. Jobs run in the worker process (no
    HTTP at all); the "last success" gauge backs a staleness alert so a silently
    dead reminder/billing job is caught.

Defensive by construction: if prometheus_client is unavailable the metrics
degrade to no-ops, and every ``record_*`` helper swallows its own errors — a
metrics hiccup must never break a payment, webhook, or cron path.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("muddato.metrics")

try:
    from prometheus_client import Counter, Gauge

    _payments = Counter(
        "muddato_payments_total",
        "Payment finalisations by method and resulting status.",
        ["method", "status"],
    )
    _webhooks = Counter(
        "muddato_webhook_requests_total",
        "Payment-provider webhook RPC outcomes by provider and result.",
        ["provider", "result"],
    )
    _cron_runs = Counter(
        "muddato_cron_runs_total",
        "Cron job executions by job id and result (ok|error).",
        ["job", "result"],
    )
    _cron_last_ok = Gauge(
        "muddato_cron_last_success_timestamp_seconds",
        "Unix timestamp of the last successful run of each cron job.",
        ["job"],
    )
    _cache = Counter(
        "muddato_cache_requests_total",
        "Read-through cache lookups by cache name and result (hit|miss).",
        ["cache", "result"],
    )
    _breaker_state = Gauge(
        "muddato_breaker_state",
        "Circuit-breaker state per outbound dependency (0=closed, 1=half_open, 2=open).",
        ["breaker"],
    )
    _breaker_trips = Counter(
        "muddato_breaker_trips_total",
        "Number of times a circuit breaker tripped to OPEN, by dependency.",
        ["breaker"],
    )
    _ratelimit_degraded = Counter(
        "muddato_ratelimit_degraded_total",
        "Rate-limit checks that hit the Redis-down degraded path, by bucket and "
        "decision (deny=fail-closed bucket refused; allow=fail-open bucket passed).",
        ["bucket", "decision"],
    )
    _webhook_dlq_depth = Gauge(
        "muddato_webhook_dlq_depth",
        "Number of webhooks in the dead-letter queue awaiting manual replay.",
    )
    _notifications = Counter(
        "muddato_notification_deliveries_total",
        "Outbound Telegram notification attempts (reminders, payment/broadcast "
        "confirmations) by kind and result (sent|failed). The DB already carries "
        "per-notification delivery truth (Notification.delivered_at, MUDD-98) but "
        "that is invisible to Prometheus — this is the rate-based signal a "
        "'spike in notification failures' alert needs (MUDD-137).",
        ["kind", "result"],
    )
    _ENABLED = True
except Exception:  # pragma: no cover - prometheus_client is a hard dep in practice
    _ENABLED = False
    log.info("prometheus_client unavailable — domain metrics are no-ops")


def record_payment(method: str, status: str) -> None:
    """Count a finalised payment (e.g. method="payme", status="approved")."""
    if not _ENABLED:
        return
    try:
        _payments.labels(method=method, status=status).inc()
    except Exception:  # never let a metric break the payment path
        log.debug("record_payment failed", exc_info=True)


def record_webhook(provider: str, result: str) -> None:
    """Count a webhook RPC outcome (provider="payme"|"click"|..., result="ok"|
    "auth_failed"|"error")."""
    if not _ENABLED:
        return
    try:
        _webhooks.labels(provider=provider, result=result).inc()
    except Exception:
        log.debug("record_webhook failed", exc_info=True)


def record_cache(name: str, result: str) -> None:
    """Count a read-through cache lookup (#256). result = "hit" | "miss"."""
    if not _ENABLED:
        return
    try:
        _cache.labels(cache=name, result=result).inc()
    except Exception:
        log.debug("record_cache failed", exc_info=True)


def record_ratelimit_degraded(bucket: str, decision: str) -> None:
    """Count a rate-limit check that fell into the Redis-down degraded path.

    decision="deny" (fail-closed bucket refused the request) or "allow"
    (fail-open bucket passed it through). A sustained rate here means the
    limiter backend (Redis) is unreachable — backs an alert (see
    ops/prometheus/muddato-alerts.yml)."""
    if not _ENABLED:
        return
    try:
        _ratelimit_degraded.labels(bucket=bucket, decision=decision).inc()
    except Exception:
        log.debug("record_ratelimit_degraded failed", exc_info=True)


def record_cron_run(job: str, result: str) -> None:
    """Count a cron execution and, on success, stamp the last-success gauge that
    backs the staleness alert."""
    if not _ENABLED:
        return
    try:
        _cron_runs.labels(job=job, result=result).inc()
        if result == "ok":
            _cron_last_ok.labels(job=job).set(time.time())
    except Exception:
        log.debug("record_cron_run failed", exc_info=True)


_BREAKER_STATE_NUM = {"closed": 0, "half_open": 1, "open": 2}


def record_breaker_state(name: str, state: str) -> None:
    """Set the circuit-breaker state gauge (#255) for an outbound dependency."""
    if not _ENABLED:
        return
    try:
        _breaker_state.labels(breaker=name).set(_BREAKER_STATE_NUM.get(state, 0))
    except Exception:
        log.debug("record_breaker_state failed", exc_info=True)


def record_breaker_trip(name: str) -> None:
    """Count a circuit-breaker trip to OPEN (#255)."""
    if not _ENABLED:
        return
    try:
        _breaker_trips.labels(breaker=name).inc()
    except Exception:
        log.debug("record_breaker_trip failed", exc_info=True)


def record_notification(kind: str, result: str) -> None:
    """Count an outbound Telegram notification attempt. result = "sent" | "failed".

    Called once per `notify_user()` call — the single funnel for per-customer
    reminders, payment-approved confirmations and broadcast deliveries — so a
    provider-side degradation shows up as a rate here well before (or even
    without) tripping the `telegram_send` circuit breaker, which only opens
    after 20 failures and says nothing about *how many* customers were missed
    in between (MUDD-137)."""
    if not _ENABLED:
        return
    try:
        _notifications.labels(kind=kind, result=result).inc()
    except Exception:
        log.debug("record_notification failed", exc_info=True)


def gauge_webhook_dlq_depth(depth: int) -> None:
    """Set the webhook DLQ depth gauge (#754).

    Called periodically to track pending webhooks awaiting manual replay.
    Alert when depth > 0 (indicates DLQ items).
    """
    if not _ENABLED:
        return
    try:
        _webhook_dlq_depth.set(depth)
    except Exception:
        log.debug("gauge_webhook_dlq_depth failed", exc_info=True)


# ──────────────────────────────────────────────────────────────────────────────
# POOL SATURATION METRICS
# ──────────────────────────────────────────────────────────────────────────────

if _ENABLED:
    _db_pool_utilization = Gauge(
        "muddato_db_pool_utilization_ratio",
        "Database connection pool utilization ratio (active_connections / pool_size).",
    )
    _redis_pool_utilization = Gauge(
        "muddato_redis_pool_utilization_ratio",
        "Redis connection pool utilization ratio (active_connections / pool_size).",
    )
    _db_active_connections = Gauge(
        "muddato_db_active_connections",
        "Current number of active database connections.",
    )
    _redis_active_connections = Gauge(
        "muddato_redis_active_connections",
        "Current number of active Redis connections.",
    )
    _db_idle_in_transaction = Gauge(
        "muddato_db_idle_in_transaction_connections",
        "Postgres backend connections for this app's login role currently in "
        "'idle in transaction' state (a transaction left open between statements).",
    )
    _db_idle_in_transaction_ratio = Gauge(
        "muddato_db_idle_in_transaction_ratio",
        "Share (0-1) of this app's Postgres backend connections that are "
        "'idle in transaction'. This is the MUDD-86 freeze signature: a request "
        "holding its transaction open (CPU-bound, or waiting on something) "
        "starves the pool well before any HTTP error rate moves.",
    )
    # (#89) prometheus-fastapi-instrumentator does NOT emit an in-flight-request
    # gauge in the version pinned here — docs/OBSERVABILITY.md previously claimed
    # it did (`http_requests_inprogress`); that claim was checked against a live
    # /metrics scrape while wiring this up and found false, which is exactly the
    # "metric that was never real" pattern this issue is about. This is a REAL
    # replacement, tracked from RequestContextMiddleware around every request.
    _http_inflight = Gauge(
        "muddato_http_requests_inprogress",
        "HTTP requests currently being handled (in-flight), independent of the "
        "duration histogram — a request stuck mid-flight behind a saturated DB "
        "pool is visible here before it ever completes (or times out).",
    )


def record_db_pool_stats(active: int, pool_size: int) -> None:
    """Update database pool saturation metrics.

    Called every 15s by the background sampler in ``app/core/pool_metrics.py``
    (MUDD-89) — not per-request. These gauges existed before that sampler did;
    nothing called them, so every scrape read the zero default.
    """
    if not _ENABLED:
        return
    try:
        _db_active_connections.set(active)
        if pool_size > 0:
            _db_pool_utilization.set(active / pool_size)
    except Exception:
        # A gauge fault must stay non-fatal, and the breadcrumb must stay at
        # debug so a broken registry cannot flood the log on every sampler tick.
        log.debug("metrics.db_pool.record_failed pool_size=%d", pool_size, exc_info=True)


def record_redis_pool_stats(active: int, pool_size: int) -> None:
    """Update Redis pool saturation metrics. Same caller/cadence as
    ``record_db_pool_stats`` above — the MUDD-89 background sampler."""
    if not _ENABLED:
        return
    try:
        _redis_active_connections.set(active)
        if pool_size > 0:
            _redis_pool_utilization.set(active / pool_size)
    except Exception:
        # Same rationale as record_db_pool_stats above.
        log.debug("metrics.redis_pool.record_failed pool_size=%d", pool_size, exc_info=True)


def inc_http_inflight() -> None:
    """Mark one more request as in-flight. Call at the very start of the request,
    paired 1:1 with ``dec_http_inflight`` in a ``finally`` (RequestContextMiddleware)."""
    if not _ENABLED:
        return
    try:
        _http_inflight.inc()
    except Exception:
        log.debug("metrics.http_inflight.inc_failed", exc_info=True)


def dec_http_inflight() -> None:
    """Pair of ``inc_http_inflight``. Never allowed to raise — a request that hit
    an unhandled error must still be able to decrement the gauge on its way out,
    or every subsequent scrape over-reports in-flight load forever."""
    if not _ENABLED:
        return
    try:
        _http_inflight.dec()
    except Exception:
        log.debug("metrics.http_inflight.dec_failed", exc_info=True)


def record_db_idle_in_transaction(idle_in_txn: int, total_connections: int) -> None:
    """Update the idle-in-transaction saturation signal (MUDD-89).

    Unlike ``record_db_pool_stats`` (pure in-memory pool counters, cheap enough
    for a per-request call), this needs a live ``pg_stat_activity`` query, so the
    caller is a periodic background sampler (``app/core/pool_metrics.py``), not
    the request path.
    """
    if not _ENABLED:
        return
    try:
        _db_idle_in_transaction.set(idle_in_txn)
        if total_connections > 0:
            _db_idle_in_transaction_ratio.set(idle_in_txn / total_connections)
    except Exception:
        log.debug("metrics.db_idle_in_transaction.record_failed", exc_info=True)
