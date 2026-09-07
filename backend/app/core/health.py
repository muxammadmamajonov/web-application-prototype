"""Readiness probe (#77).

``/health`` is *liveness* (the process is up). ``/ready`` (this module) is
*readiness* — it actively checks DB + Redis. The database is critical; Redis is
reported as degraded but does not drain an otherwise healthy API instance,
because cache and non-auth rate-limit paths already fail open.
"""
from __future__ import annotations

import logging

log = logging.getLogger("muddato.health")


async def check_db() -> bool:
    from sqlalchemy import text

    from app.db import engine
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        log.warning("health.db_unreachable", exc_info=True)
        return False


async def check_redis() -> bool:
    from app.core.redis import get_redis
    try:
        await get_redis().ping()
        return True
    except Exception:
        log.warning("health.redis_unreachable", exc_info=True)
        return False


async def readiness() -> tuple[bool, dict[str, str]]:
    """Return ``(can_serve, per-dependency status)`` for ``/ready``.

    DB failure makes the service unavailable. Redis failure remains visible in
    the payload/metrics but must not cause every machine to be removed from the
    load balancer at once.
    """
    db_ok = await check_db()
    redis_ok = await check_redis()
    checks = {"db": "ok" if db_ok else "down", "redis": "ok" if redis_ok else "down"}
    return db_ok, checks
