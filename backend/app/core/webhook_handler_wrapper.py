"""Webhook handler wrapper with automatic DLQ error handling (#754).

Provides a decorator that wraps webhook handlers to automatically:
- Catch exceptions
- Classify errors (transient vs permanent)
- Move failed webhooks to DLQ
- Record metrics and logs

STATUS: BUILT, NOT WIRED. Nothing decorates a handler with this today — the
webhook DLQ *wiring* is a deferred item from the 2026-06 backend audit, and this
is the half of it that exists. The dead-code sweep in Batch 6 found it and
deliberately kept it: deleting a deferred feature's scaffolding does not close
the deferral, it just makes the next person build it again.

It is annotated rather than removed so that "unused" and "unfinished" stop
looking identical from the outside. The DLQ table and its writer
(`app/core/webhook_retry.py`) ARE live; only this decorator is unattached.

To finish it: decorate the provider handlers in `app/api/webhooks/` and add the
denial/replay tests. Until then, treat any reference to it as a mistake.
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.webhook_error import handle_webhook_error

log = logging.getLogger("muddato.webhook_handler")


def webhook_handler(
    provider: str,
    method: str,
) -> Callable:
    """Decorator for webhook endpoint handlers.

    Wraps the handler to catch exceptions and move failures to DLQ.

    Example:
        @router.post("/prepare")
        @webhook_handler(provider="click", method="prepare")
        async def prepare(...):
            # Handler logic here
            return {...}
    """
    def decorator(handler: Callable) -> Callable:
        @functools.wraps(handler)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Extract db from kwargs (FastAPI dependency injection)
            db = kwargs.get('db')
            if not db:
                # If no db in kwargs, try positional args
                # This is fragile but works for simple cases
                for arg in args:
                    if isinstance(arg, AsyncSession):
                        db = arg
                        break
            
            try:
                # Call the original handler
                return await handler(*args, **kwargs)
            except Exception as err:
                log.error(
                    "webhook_handler.error provider=%s method=%s err=%s",
                    provider, method, str(err)[:100],
                    exc_info=err,
                )
                
                if db is None:
                    # No db available; re-raise so FastAPI returns 500
                    raise
                
                # Extract payload from the request (FormData from FastAPI)
                # This is a simplified approach; adjust as needed
                payload = {
                    "provider": provider,
                    "method": method,
                    "error_class": type(err).__name__,
                }
                
                # Try to handle the error
                response, _status = await handle_webhook_error(
                    db,
                    err,
                    provider=provider,
                    method=method,
                    payload=payload,
                    tenant_id=None,
                )
                
                # Return the error response
                # Note: We'd need to return a FastAPI Response object
                # For now, just return the dict (FastAPI will auto-convert)
                return response
        
        return wrapper
    
    return decorator
