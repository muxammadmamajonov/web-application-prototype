"""Idempotency key tracking to prevent duplicate operations in concurrent scenarios.

Allows tracking the result of a mutation so replayed requests return the same
result without duplicate side effects. Essential for:
- Cancellation operations that race with incoming payments
- Webhook retries
- Network-level request retries

Stores the operation result in Redis with a TTL so replayed requests can return
the cached result instead of re-executing.

NOTE — scope & guarantees (read before wiring this into a handler):
  * This is a *secondary, fail-open* layer and is **not currently used** by any
    handler. The live idempotency for money paths is DB-enforced — row
    ``SELECT … FOR UPDATE`` locks plus unique constraints (see cancel_installment
    and the top-up credit path) — which remains the source of truth.
  * Redis errors are intentionally swallowed (fail-open): on a Redis outage these
    helpers degrade to *no* deduplication rather than blocking the operation.
  * ``with_idempotency`` is check-then-act (no atomic ``SET NX``). It deduplicates
    *sequential* replays/retries, but does **not** by itself stop two genuinely
    *simultaneous* in-flight calls from both executing — add an atomic
    reservation before depending on it for concurrency control.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.redis import get_redis

log = logging.getLogger("muddato.idempotency")

# Idempotency key TTL: 1 hour (covers typical retry windows)
IDEMPOTENCY_TTL = 3600


async def idempotency_key(
    *,
    scope: str,
    operation: str,
    entity_id: str,
    tenant_id: str | None = None,
) -> str:
    """Generate a unique idempotency key from scope + operation + entity.

    For multi-tenant deployments, include tenant_id to avoid collisions.
    Keys are tenant-scoped: "tenant:scope:operation:entity_id"

    Example:
        idempotency_key(
            scope="subscription", 
            operation="cancel", 
            entity_id="sub-123",
            tenant_id="tenant-456"
        )
        → "tenant-456:subscription:cancel:sub-123"
    
    Without tenant_id (for single-tenant operations):
        → "subscription:cancel:sub-123"
    """
    if tenant_id:
        return f"{tenant_id}:{scope}:{operation}:{entity_id}"
    return f"{scope}:{operation}:{entity_id}"


async def check_idempotency(key: str) -> dict[str, Any] | None:
    """Check if an operation with this key has already been processed.

    Returns:
        Cached result dict if found (idempotent): {"success": bool, "result": any}
        None if not yet processed (new operation).
    """
    rds = get_redis()
    try:
        cached = await rds.get(key)
        if cached is not None:
            import orjson
            return orjson.loads(cached)
    except Exception as e:
        log.debug("idempotency.check_failed key=%s", key, exc_info=e)
    return None


async def record_idempotency(
    key: str,
    result: dict[str, Any],
) -> None:
    """Record that an operation succeeded, for future replay checks.

    Args:
        key: The idempotency key (from idempotency_key())
        result: Dict with at minimum {"success": bool}

    Example:
        await record_idempotency(
            "sub:cancel:123-456",
            {"success": True, "cancelled_at": "2026-06-12T..."}
        )
    """
    rds = get_redis()
    try:
        import orjson
        await rds.set(
            key,
            orjson.dumps(result),
            ex=IDEMPOTENCY_TTL,
        )
    except Exception as e:
        log.debug("idempotency.record_failed key=%s", key, exc_info=e)


async def with_idempotency(
    key: str,
    operation_fn,
    *args,
    **kwargs,
) -> dict[str, Any]:
    """Execute an operation with idempotency wrapping.

    If the idempotency key has been seen before, returns the cached result.
    Otherwise, calls operation_fn(*args, **kwargs), caches the result, and returns it.

    Args:
        key: Idempotency key (from idempotency_key())
        operation_fn: Async callable to execute
        *args, **kwargs: Arguments to pass to operation_fn

    Returns:
        Operation result: {"success": bool, ...}

    Example:
        result = await with_idempotency(
            idempotency_key(scope="sub", operation="cancel", entity_id=str(sub_id)),
            my_cancel_subscription,
            db=db,
            subscription_id=sub_id,
        )
    """
    # Check if already processed
    cached = await check_idempotency(key)
    if cached is not None:
        log.info("idempotency.replay key=%s", key)
        return cached

    # Execute operation
    result = await operation_fn(*args, **kwargs)

    # Record for future replays
    await record_idempotency(key, result)

    return result
