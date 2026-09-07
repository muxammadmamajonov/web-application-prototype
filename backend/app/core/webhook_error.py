"""Webhook error handling and DLQ integration (#754).

Wraps webhook handlers to catch exceptions, determine if retryable, and
move to DLQ if unrecoverable.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.webhook_retry import move_to_dlq

log = logging.getLogger("muddato.webhook_error")


class WebhookError(Exception):
    """Base class for webhook errors."""
    
    def __init__(
        self,
        message: str,
        code: int = -999,
        retryable: bool = False,
    ):
        self.message = message
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class TransientWebhookError(WebhookError):
    """Transient errors that should trigger a retry (timeout, 502, etc.)."""
    
    def __init__(self, message: str, code: int = -999):
        super().__init__(message, code=code, retryable=True)


class PermanentWebhookError(WebhookError):
    """Permanent errors that should NOT retry (bad signature, missing service, etc.)."""
    
    def __init__(self, message: str, code: int = -999):
        super().__init__(message, code=code, retryable=False)


async def handle_webhook_error(
    db: AsyncSession,
    err: Exception,
    *,
    provider: str,
    method: str,
    payload: dict,
    tenant_id: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Handle a webhook error, potentially moving to DLQ if not retryable.

    Args:
        db: Async session
        err: The caught exception
        provider: "click", "payme", etc.
        method: "prepare", "complete", etc.
        payload: Original webhook payload
        tenant_id: Tenant context

    Returns:
        (response_dict, http_status_code)
    """
    error_msg = str(err)[:100]
    
    # Determine if this is retryable
    retryable = isinstance(err, TransientWebhookError)
    
    if not retryable and isinstance(err, WebhookError):
        # Log permanent errors  but don't move to DLQ yet
        # (the calling handler should have already returned an error response)
        log.warning(
            "webhook.permanent_error provider=%s method=%s error=%s",
            provider, method, error_msg,
        )
        if hasattr(err, 'code'):
            return {"error": err.code, "error_note": err.message}, 400
        return {"error": -999, "error_note": error_msg}, 400
    
    if retryable:
        # Transient error - move to DLQ for later replay
        log.warning(
            "webhook.transient_error_dlq provider=%s method=%s error=%s",
            provider, method, error_msg,
        )
        await move_to_dlq(
            db,
            provider=provider,
            method=method,
            payload=payload,
            tenant_id=tenant_id,
            error=error_msg,
        )
        return {"error": -999, "error_note": "service_unavailable_retry_later"}, 503
    
    # Unknown error
    log.error(
        "webhook.unknown_error provider=%s method=%s error=%s",
        provider, method, error_msg, exc_info=err,
    )
    return {"error": -999, "error_note": "internal_error"}, 500
