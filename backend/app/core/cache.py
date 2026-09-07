"""Redis read-through cache for hot, stale-tolerant reads (#256).

get-or-compute: serve a cached JSON value when warm, else run ``compute()``,
cache it (short TTL + a little jitter to avoid synchronized stampedes), and
return it. Keys are caller-supplied and MUST be tenant-scoped for tenant data.
**Fail-open**: any Redis error falls through to ``compute()`` — a cache outage
must never break a read.

NEVER cache money-authoritative single-record reads used for a payment decision
(those must read committed rows, often under a lock). This is for list/aggregate
*view* reads where a few seconds of staleness is acceptable; document the
staleness window on the endpoint.
"""
from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import orjson

from app.core.redis import get_redis

log = logging.getLogger("muddato.cache")


async def cached_json(
    key: str,
    ttl: int,
    compute: Callable[[], Awaitable[Any]],
    *,
    label: str = "cache",
    jitter: float = 0.2,
) -> Any:
    """Return ``key`` from Redis if warm, else ``await compute()`` and cache it.

    ``ttl`` seconds (spread by up to ``jitter`` to de-synchronise expiry).
    ``label`` is the low-cardinality cache name for the hit/miss metric. Never
    raises on a Redis fault — degrades to a direct compute.
    """
    rds = get_redis()
    try:
        hit = await rds.get(key)
        if hit is not None:
            _record(label, "hit")
            return orjson.loads(hit)
    except Exception:
        log.debug("cache.read_failed key=%s", key, exc_info=True)

    _record(label, "miss")
    value = await compute()
    try:
        eff_ttl = max(1, int(ttl * (1 + random.uniform(0, jitter))))
        await rds.set(key, orjson.dumps(value), ex=eff_ttl)
    except Exception:
        log.debug("cache.write_failed key=%s", key, exc_info=True)
    return value


async def invalidate(*keys: str) -> None:
    """Best-effort delete of cache keys (e.g. after a write that the TTL window
    shouldn't outlive). No-op on a Redis fault."""
    if not keys:
        return
    try:
        await get_redis().delete(*keys)
    except Exception:
        log.debug("cache.invalidate_failed", exc_info=True)


def _record(label: str, result: str) -> None:
    try:
        from app.core.metrics import record_cache
        record_cache(label, result)
    except Exception:
        # Cache hit/miss accounting is best-effort and must never surface to the
        # caller. Debug matches the read/write fault breadcrumbs above; this runs
        # on every cache access, so anything louder would flood.
        log.debug("cache.metrics.record_failed label=%s result=%s", label, result, exc_info=True)
