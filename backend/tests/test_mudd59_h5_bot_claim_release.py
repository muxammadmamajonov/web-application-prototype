"""(MUDD-59 H5) A bot-handler crash must not permanently swallow a Telegram
update.

Before the fix, `telegram_webhook` / `tenant_telegram_webhook` only handled
`TimeoutError` around `feed_update`. Any other exception propagated straight
out of the endpoint (Telegram sees a 500 and retries — that part was fine),
but the Redis dedup claim taken by `_claim_update` at the top of the handler
was never released. So the retry hit `_claim_update` → False → the endpoint
replied `{"ok": True}` without ever calling the handler again: the update was
lost and logged as an indistinguishable "duplicate".

These tests reproduce the crash against the REAL `_claim_update` /
`_release_claim` helpers (not a rewritten stand-in for them) with an
in-memory fake Redis, so a regression in the SET-NX/DEL pairing itself would
fail them too.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.bot import _shared_secret, _tenant_secret, telegram_webhook, tenant_telegram_webhook


class _FakeRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


def _update_payload(update_id: int) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 0,
            "chat": {"id": 1, "type": "private"},
            "text": "/start",
        },
    }


def _fake_redis() -> MagicMock:
    """SET NX / DEL against an in-memory set — proves a real release, not a mock call."""
    claimed: set[str] = set()

    async def _set(key, _value, nx=False, ex=None):
        if nx and key in claimed:
            return None
        claimed.add(key)
        return True

    async def _delete(key):
        claimed.discard(key)

    client = MagicMock()
    client.set = AsyncMock(side_effect=_set)
    client.delete = AsyncMock(side_effect=_delete)
    client.claimed = claimed
    return client


async def test_platform_webhook_crash_releases_claim_for_retry() -> None:
    client = _fake_redis()
    crash = AsyncMock(side_effect=RuntimeError("handler exploded"))
    with (
        patch("app.core.redis.get_redis", return_value=client),
        patch("app.api.bot._dp.feed_update", crash),
    ):
        secret = _shared_secret()
        req = _FakeRequest(_update_payload(4242))

        # First delivery: handler crashes. The endpoint must still propagate
        # the error (so Telegram sees a non-2xx and retries) — that part
        # already worked before the fix.
        with pytest.raises(RuntimeError):
            await telegram_webhook(req, secret)

        # OLD BUG: "tg:upd:platform:4242" stayed claimed here, so this
        # assertion is what turns RED on the pre-fix code.
        assert "tg:upd:platform:4242" not in client.claimed, (
            "a handler crash must release the dedup claim so Telegram's retry "
            "is reprocessed, not silently dropped as a 'duplicate'"
        )

        # Second delivery (Telegram's retry, same update_id): must actually
        # reach the handler again, not short-circuit on `_claim_update`.
        crash.reset_mock()
        crash.side_effect = None
        crash.return_value = None
        req2 = _FakeRequest(_update_payload(4242))
        result = await telegram_webhook(req2, secret)
        assert result == {"ok": True}
        crash.assert_awaited_once()


async def test_platform_webhook_timeout_still_swallowed_not_released() -> None:
    """Unchanged control case: a timeout is NOT a crash — the update keeps its
    claim (no immediate retry storm) exactly as before this fix."""
    client = _fake_redis()

    async def _hang(*_a, **_k):
        import asyncio
        await asyncio.sleep(10)

    with (
        patch("app.core.redis.get_redis", return_value=client),
        patch("app.api.bot._dp.feed_update", AsyncMock(side_effect=_hang)),
        patch("app.api.bot._WEBHOOK_TIMEOUT", 0.01),
    ):
        secret = _shared_secret()
        req = _FakeRequest(_update_payload(777))
        result = await telegram_webhook(req, secret)
        assert result == {"ok": True}
        assert "tg:upd:platform:777" in client.claimed


async def test_tenant_webhook_crash_releases_claim_for_retry() -> None:
    """Same fix applied to the per-tenant webhook (the issue flagged both call sites)."""
    from uuid import uuid4

    tenant_id = uuid4()
    client = _fake_redis()
    crash = AsyncMock(side_effect=RuntimeError("tenant handler exploded"))

    fake_bot = object()

    class _FakeDB:
        async def execute(self, *_a, **_k):
            raise AssertionError("not used — _get_tenant_bot is patched directly")

    with (
        patch("app.core.redis.get_redis", return_value=client),
        patch("app.api.bot._get_tenant_bot", AsyncMock(return_value=fake_bot)),
        patch("app.api.bot._get_tenant_dispatcher") as get_dp,
    ):
        dp = MagicMock()
        dp.feed_update = crash
        get_dp.return_value = dp

        secret = _tenant_secret(tenant_id)
        req = _FakeRequest(_update_payload(55))
        with pytest.raises(RuntimeError):
            await tenant_telegram_webhook(tenant_id, req, _FakeDB(), secret)

        assert f"tg:upd:{tenant_id}:55" not in client.claimed
