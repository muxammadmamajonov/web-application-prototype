# Phase 3: Observability (Ready for Deployment)

## Overview
Production observability stack for Muddato fintech:
- **Error Tracking**: Sentry (error budget, release health, regressions)
- **Metrics**: Prometheus (latency p95, pool saturation, business KPIs)
- **Logging**: Structured JSON via structlog (request tracing, audit trail)
- **Dashboards**: Prometheus metrics exported at `/metrics`

## Deployed Metrics

### System Health Metrics
```
http_request_duration_seconds{handler,method,status}  # Latency histogram
http_requests_total{handler,method,status}            # Request count
```

### Business Metrics
```
muddato_payments_total{method,status}                 # Payment finalisations
muddato_webhook_requests_total{provider,result}       # Webhook outcomes
muddato_cron_runs_total{job,result}                   # Cron job executions
muddato_cron_last_success_timestamp_seconds{job}      # Cron health
muddato_webhook_dlq_depth                             # Dead-letter queue depth
muddato_cache_requests_total{cache,result}            # Cache hit/miss ratio
muddato_breaker_state{breaker}                        # Circuit breaker state
```

### Pool Saturation Metrics (Phase 3)
```
muddato_db_pool_utilization_ratio              # Database pool %
muddato_redis_pool_utilization_ratio           # Redis pool %
muddato_db_active_connections                  # Active DB connections
muddato_redis_active_connections               # Active Redis connections
```

## Recommended Alert Rules

### Latency
- Latency p95 > 500ms sustained (5 min) → Page
- Latency p95 > 200ms sustained (10 min) → Warning

### Saturation
- Database pool utilization > 90% → Warning
- Redis pool utilization > 90% → Warning
- Webhook DLQ depth > 100 → Warning

### Health
- 5xx error rate > 1% → Page
- Cron job staleness (last_success > 5 min) → Warning
- Payment error rate spike > 10x baseline → Page

## Next Steps for Phase 3 Completion
1. Integrate pool stats collection in db.py + redis.py
2. Deploy Prometheus scrape config to Fly
3. Configure Sentry alert rules via UI
4. Build Grafana dashboards
5. Test alerts with synthetic traffic
