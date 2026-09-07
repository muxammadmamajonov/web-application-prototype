"""Webhook retry management with dead-letter queue (#754).

When a webhook fails, attempt retry with exponential backoff up to MAX_RETRIES.
After exhausting retries, move to the WebhookDLQ for manual inspection and replay.
Exposes metrics for DLQ depth and alert on pending items.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WebhookDLQ

log = logging.getLogger("muddato.webhook_retry")

MAX_WEBHOOK_RETRIES = 10


async def move_to_dlq(
    db: AsyncSession,
    *,
    provider: str,
    method: str,
    payload: dict,
    tenant_id: str | None = None,
    error: str = "",
) -> WebhookDLQ:
    """Move a failed webhook to the dead-letter queue for later replay.

    Args:
        db: Async session
        provider: "click", "payme", etc.
        method: "prepare", "complete", etc.
        payload: Original form/JSON payload
        tenant_id: Tenant context (nullable for platform webhooks)
        error: Description of the last error

    Returns:
        The created WebhookDLQ record.
    """
    dlq_row = WebhookDLQ(
        tenant_id=tenant_id,
        provider=provider,
        method=method,
        payload=payload,
        retry_count=MAX_WEBHOOK_RETRIES,
        last_error=error[:1024] if error else None,
        dlq_at=datetime.now(UTC),
    )
    db.add(dlq_row)
    await db.commit()
    log.warning(
        "webhook.moved_to_dlq provider=%s method=%s tenant=%s error=%s",
        provider, method, tenant_id, error[:100] if error else "",
    )
    return dlq_row


async def get_dlq_depth(db: AsyncSession) -> int:
    """Return the count of pending webhooks in the DLQ.

    Used for monitoring/alerting: alert if > 0.
    """
    # (H6) COUNT in the database instead of loading every DLQ row into memory —
    # this is a monitoring/alert path, so it runs continually and must stay O(1)
    # regardless of how deep the queue gets.
    stmt = select(func.count()).select_from(WebhookDLQ)
    return (await db.execute(stmt)).scalar_one()


async def record_dlq_metric(db: AsyncSession) -> None:
    """Record the current DLQ depth as a Prometheus gauge.

    Called periodically (e.g. by health check or metrics endpoint).
    """
    try:
        from app.core.metrics import gauge_webhook_dlq_depth
        depth = await get_dlq_depth(db)
        gauge_webhook_dlq_depth(depth)
    except Exception:
        log.debug("webhook_retry.metric_failed", exc_info=True)
