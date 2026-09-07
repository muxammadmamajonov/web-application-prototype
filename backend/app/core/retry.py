"""Retry-with-backoff for IDEMPOTENT outbound calls (#149).

Use ONLY for operations that are safe to repeat. Never wrap SMS-send (Eskiz —
a retry double-texts/charges) or the single-use OIDC code exchange (a retry fails
with the code already consumed). For those, a transient failure must surface, not
retry.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx

log = logging.getLogger("muddato.retry")


async def retry_http[T](
    make: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    label: str = "outbound",
) -> T:
    """Await ``make()``, retrying transient httpx errors with exponential backoff
    (base_delay, doubling each attempt). Re-raises the last error after ``attempts``
    tries. The callable must be IDEMPOTENT — it may run up to ``attempts`` times."""
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return await make()
        except httpx.HTTPError as e:
            last = e
            if i == attempts - 1:
                break
            delay = base_delay * (2 ** i)
            log.warning(
                "retry.%s attempt=%d/%d err=%s backoff=%.1fs",
                label, i + 1, attempts, type(e).__name__, delay,
            )
            await asyncio.sleep(delay)
    assert last is not None  # unreachable on success; narrows for the re-raise
    raise last
