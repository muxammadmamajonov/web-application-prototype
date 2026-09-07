"""Single shared async Redis client."""
from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

from app.config import settings
from app.core.breaker import BreakerOpenError, redis_breaker

_client: Redis | None = None

# (#H2) `Redis.from_url` had no connection cap. A slow/unreachable Upstash
# means concurrent callers each open (or block opening) another socket with
# nothing bounding how many accumulate — the pool itself can then become part
# of the outage instead of just suffering from it. Sized generously above
# realistic concurrent Redis callers on one app instance (rate limiting,
# idempotency, cache, OIDC state, SMS dedup), not tuned to a load test.
_MAX_CONNECTIONS = 50


class _BreakerRedis(Redis):
    """``Redis`` that fails fast via ``redis_breaker`` instead of paying the
    full ``socket_timeout`` on every call once the backend is known to be down
    (#H2).

    Every redis-py command (plain commands, ``EVAL``, ...) funnels through
    ``execute_command`` — wrapping it here means every existing call site
    (rate limiting, idempotency, cache, OIDC state, SMS dedup) gets breaker
    protection with no change to any of them. Every one of those call sites
    already treats any exception from a Redis call as "backend unavailable"
    (fail-open or fail-closed per its own policy) — ``BreakerOpenError`` is
    just another exception to them, so this changes latency under a sustained
    outage (fail fast instead of a 5s wait per call), not behaviour.
    """

    async def execute_command(self, *args: Any, **options: Any) -> Any:
        if not redis_breaker.allow():
            raise BreakerOpenError(redis_breaker.name)
        try:
            result = await super().execute_command(*args, **options)
        except Exception:
            redis_breaker.record_failure()
            raise
        redis_breaker.record_success()
        return result


def get_redis() -> Redis:
    global _client
    if _client is None:
        # (#77) Socket timeouts so a hung/unreachable Redis can't block a request
        # forever; keepalive + periodic health check recycle dead pooled sockets
        # (important behind Upstash's serverless proxy).
        _client = _BreakerRedis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            socket_keepalive=True,
            health_check_interval=30,
            max_connections=_MAX_CONNECTIONS,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()  # type: ignore[attr-defined]  # redis-py stub lacks aclose() (exists at runtime)
        _client = None
