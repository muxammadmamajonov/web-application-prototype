"""#77 — readiness probe (DB+Redis) and Redis client socket timeouts."""
from __future__ import annotations

import pytest

from app.core import health


async def test_readiness_all_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok() -> bool:
        return True

    monkeypatch.setattr(health, "check_db", _ok)
    monkeypatch.setattr(health, "check_redis", _ok)
    ok, checks = await health.readiness()
    assert ok is True
    assert checks == {"db": "ok", "redis": "ok"}


async def test_readiness_db_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok() -> bool:
        return True

    async def _down() -> bool:
        return False

    monkeypatch.setattr(health, "check_db", _down)
    monkeypatch.setattr(health, "check_redis", _ok)
    ok, checks = await health.readiness()
    assert ok is False
    assert checks["db"] == "down"
    assert checks["redis"] == "ok"


async def test_readiness_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok() -> bool:
        return True

    async def _down() -> bool:
        return False

    monkeypatch.setattr(health, "check_db", _ok)
    monkeypatch.setattr(health, "check_redis", _down)
    ok, checks = await health.readiness()
    # Redis-backed cache/expensive-route limits fail open by design. Draining
    # every API machine would turn that degradation into a total outage.
    assert ok is True
    assert checks["redis"] == "down"


def test_redis_client_has_socket_timeouts() -> None:
    """A hung Redis connection must not block a request forever (#77)."""
    from app.core.redis import get_redis

    kw = get_redis().connection_pool.connection_kwargs
    assert kw.get("socket_timeout") == 5
    assert kw.get("socket_connect_timeout") == 5
