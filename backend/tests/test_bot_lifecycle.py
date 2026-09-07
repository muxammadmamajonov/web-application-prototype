"""Telegram HTTP sessions are released during app and worker shutdown."""
from __future__ import annotations

import inspect
from uuid import uuid4

import pytest


class _Session:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


class _Bot:
    def __init__(self) -> None:
        self.session = _Session()


@pytest.mark.asyncio
async def test_real_telegram_transport_is_blocked() -> None:
    from aiogram import Bot

    bot = Bot(token="123456:TEST-TOKEN")
    try:
        with pytest.raises(pytest.fail.Exception, match="real Telegram request"):
            await bot.send_message(chat_id=1, text="must not leave the test process")
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_notification_bot_is_closed_and_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import notifications

    bot = _Bot()
    monkeypatch.setattr(notifications, "_bot", bot)

    await notifications.close_bot()

    assert bot.session.closed == 1
    assert notifications._bot is None


@pytest.mark.asyncio
async def test_api_shutdown_closes_platform_and_cached_tenant_bots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import bot as bot_api

    platform = _Bot()
    tenant_a = _Bot()
    tenant_b = _Bot()
    cache = bot_api._TTLCache(ttl=60, maxsize=3)
    cache.set(uuid4(), tenant_a)
    cache.set(uuid4(), tenant_b)
    monkeypatch.setattr(bot_api, "_bot", platform)
    monkeypatch.setattr(bot_api, "_tenant_bots", cache)

    await bot_api.close_bots()

    assert platform.session.closed == 1
    assert tenant_a.session.closed == 1
    assert tenant_b.session.closed == 1
    assert cache.values() == []


def test_web_and_worker_shutdown_paths_release_bot_sessions() -> None:
    from app import main, worker

    lifespan_source = inspect.getsource(main.lifespan)
    worker_source = inspect.getsource(worker.run)

    assert "close_bots" in lifespan_source
    assert "close_notification_bot" in lifespan_source
    assert "close_notification_bot" in worker_source
