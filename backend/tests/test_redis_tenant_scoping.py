"""#750 — Redis keys are tenant-scoped (no cross-tenant collision).

Audit of #750 ("Telegram user_id collision in shared Redis cache — allows
reading other tenant's messages"): there is **no Redis message cache**. Messages
and notifications live in Postgres under RLS (see test_rls_isolation / test_p2_rls).
Every Redis key in the app is scoped by tenant_id, a globally-unique id, or a
random single-use token.

These tests lock the two keys the issue is actually about — the Telegram
webhook-dedup claim and the idempotency key — so a refactor can't silently drop
the tenant scope and *introduce* the collision #750 fears.
"""
from __future__ import annotations

import asyncio


async def test_idempotency_key_is_tenant_scoped() -> None:
    from app.core.idempotency import idempotency_key

    ka = await idempotency_key(
        scope="subscription", operation="cancel", entity_id="sub-1", tenant_id="tenant-A",
    )
    kb = await idempotency_key(
        scope="subscription", operation="cancel", entity_id="sub-1", tenant_id="tenant-B",
    )
    # Same operation + entity under different tenants → distinct keys.
    assert ka != kb
    assert ka.startswith("tenant-A:")
    assert kb.startswith("tenant-B:")


class _RecordingRedis:
    """Captures the keys passed to SET so we can assert on their scoping."""

    def __init__(self) -> None:
        self.keys: list[str] = []

    async def set(self, key: str, *args: object, **kwargs: object) -> bool:
        self.keys.append(key)
        return True


def test_webhook_update_dedup_is_tenant_scoped(monkeypatch) -> None:
    """The SAME Telegram update_id arriving on two different tenant bots must
    claim DISTINCT Redis keys — otherwise tenant B's webhook would be deduped
    against tenant A's, a cross-tenant collision (#750). The handler passes
    str(tenant_id) as the scope for tenant bots and "platform" for the shared bot."""
    import app.core.redis as redis_mod
    from app.api.bot import _claim_update

    rec = _RecordingRedis()
    monkeypatch.setattr(redis_mod, "get_redis", lambda: rec)

    async def run() -> None:
        await _claim_update("tenant-A", 42)
        await _claim_update("tenant-B", 42)  # same update_id, different tenant
        await _claim_update("platform", 42)

    asyncio.run(run())

    assert len(rec.keys) == 3
    assert len(set(rec.keys)) == 3, f"keys collided across scopes: {rec.keys}"
    assert "tenant-A" in rec.keys[0]
    assert "tenant-B" in rec.keys[1]
    assert "platform" in rec.keys[2]
