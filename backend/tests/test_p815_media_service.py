"""#815 — the Telegram media service maps every failure correctly and fails fast.

The whole point of the breaker + timeout wrapping is that a Telegram outage returns
a clean ``TelegramMediaUnavailable`` (→ the proxy falls back / 503s) instead of
hanging a request, while a per-file problem is a ``TelegramMediaError``. Only the
happy path + breaker-open + getFile-oversize were covered; these pin the rest of the
error mapping and the 20 MB ceiling on **both** directions.

Uses a small configurable fake ``Bot`` (no network), same style as
``test_telegram_media.FakeBot``.
"""
from __future__ import annotations

import asyncio
import io

import pytest
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

# ── fakes ────────────────────────────────────────────────────────────────────

class _Doc:
    file_id = "FID"
    file_unique_id = "UID"
    file_name = "f.pdf"
    mime_type = "application/pdf"
    file_size = 10


class _Msg:
    message_id = 1
    document = _Doc()


class _File:
    def __init__(self, file_size, file_path="documents/f.pdf") -> None:
        self.file_size = file_size
        self.file_path = file_path


class _ApiError(TelegramAPIError):
    """A Telegram API error with a controllable description (real ones need a
    TelegramMethod instance to construct)."""

    def __init__(self, message: str = "Bad Request: boom") -> None:
        Exception.__init__(self, message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class _Flood(TelegramRetryAfter):
    def __init__(self, retry_after: int = 1) -> None:
        Exception.__init__(self, "Too Many Requests")
        self.retry_after = retry_after


class _Bot:
    """Records calls; each behaviour (raise / sleep / canned result) is opt-in."""

    def __init__(self, *, send_results=None, getfile_result=None,
                 download_bytes: bytes = b"DATA", send_sleep: float = 0.0,
                 getfile_sleep: float = 0.0) -> None:
        self._send_results = send_results       # list of (Exception|None) per call
        self._sendi = 0
        self._getfile_result = getfile_result   # Exception | _File | None
        self._download = download_bytes
        self._send_sleep = send_sleep
        self._getfile_sleep = getfile_sleep
        self.sends = 0

    async def send_document(self, chat, *, document=None, message_thread_id=None, caption=None):
        self.sends += 1
        if self._send_sleep:
            await asyncio.sleep(self._send_sleep)
        if self._send_results is not None:
            r = self._send_results[self._sendi]
            self._sendi += 1
            if isinstance(r, Exception):
                raise r
        return _Msg()

    async def get_file(self, file_id):
        if self._getfile_sleep:
            await asyncio.sleep(self._getfile_sleep)
        if isinstance(self._getfile_result, Exception):
            raise self._getfile_result
        return self._getfile_result or _File(10)

    async def download_file(self, file_path):
        return io.BytesIO(self._download)


@pytest.fixture(autouse=True)
def _isolate_breaker(monkeypatch):
    # Default the breaker CLOSED + make accounting a no-op so tests don't pollute
    # each other's breaker state. Individual tests override allow() where needed.
    from app.core import breaker

    monkeypatch.setattr(breaker.telegram_breaker, "allow", lambda: True)
    monkeypatch.setattr(breaker.telegram_breaker, "record_failure", lambda: None)
    monkeypatch.setattr(breaker.telegram_breaker, "record_success", lambda: None)


# ── send: ceiling + error mapping ────────────────────────────────────────────

async def test_send_rejects_oversize_before_any_api_call() -> None:
    from app.config import settings
    from app.services.telegram_media import TelegramMediaError, send_document_to_topic

    bot = _Bot()
    big = b"x" * (settings.telegram_media_max_bytes + 1)
    with pytest.raises(TelegramMediaError):
        await send_document_to_topic(big, filename="f.pdf", thread_id=1, chat_id=-100, bot=bot)
    assert bot.sends == 0   # rejected before touching Telegram — never sent un-retrievable bytes


async def test_send_timeout_maps_to_unavailable(monkeypatch) -> None:
    from app.services import telegram_media
    from app.services.telegram_media import TelegramMediaUnavailable, send_document_to_topic

    monkeypatch.setattr(telegram_media, "_SEND_TIMEOUT", 0.05)
    bot = _Bot(send_sleep=1.0)          # exceeds the timeout → asyncio.timeout fires
    with pytest.raises(TelegramMediaUnavailable):
        await send_document_to_topic(b"x", filename="f.pdf", thread_id=1, chat_id=-100, bot=bot)


async def test_send_api_error_maps_to_media_error() -> None:
    from app.services.telegram_media import (
        TelegramMediaError,
        TelegramMediaUnavailable,
        send_document_to_topic,
    )

    bot = _Bot(send_results=[_ApiError("Bad Request: chat not found")])
    with pytest.raises(TelegramMediaError) as e:
        await send_document_to_topic(b"x", filename="f.pdf", thread_id=1, chat_id=-100, bot=bot)
    assert not isinstance(e.value, TelegramMediaUnavailable)   # a per-file error, not an outage


async def test_send_honours_flood_control_and_retries_once(monkeypatch) -> None:
    from app.services import telegram_media
    from app.services.telegram_media import send_document_to_topic

    async def _noop(_delay):
        return None

    monkeypatch.setattr(telegram_media.asyncio, "sleep", _noop)   # don't actually wait
    bot = _Bot(send_results=[_Flood(retry_after=1), None])        # flood, then success
    ref = await send_document_to_topic(b"x", filename="f.pdf", thread_id=1, chat_id=-100, bot=bot)
    assert ref.file_id == "FID"
    assert bot.sends == 2   # retried exactly once after the flood wait


# ── download: breaker + error mapping + post-download ceiling ─────────────────

async def test_download_breaker_open_maps_to_unavailable(monkeypatch) -> None:
    from app.core import breaker
    from app.services.telegram_media import TelegramMediaUnavailable, download_document

    monkeypatch.setattr(breaker.telegram_breaker, "allow", lambda: False)
    with pytest.raises(TelegramMediaUnavailable):
        await download_document("FID", bot=_Bot(), max_bytes=1000)


async def test_download_api_error_maps_to_unavailable() -> None:
    from app.services.telegram_media import TelegramMediaUnavailable, download_document

    bot = _Bot(getfile_result=_ApiError("Bad Request: file is gone"))
    with pytest.raises(TelegramMediaUnavailable):
        await download_document("FID", bot=bot, max_bytes=1000)


async def test_download_timeout_maps_to_unavailable(monkeypatch) -> None:
    from app.services import telegram_media
    from app.services.telegram_media import TelegramMediaUnavailable, download_document

    monkeypatch.setattr(telegram_media, "_DOWNLOAD_TIMEOUT", 0.05)
    bot = _Bot(getfile_sleep=1.0)       # getFile hangs past the timeout
    with pytest.raises(TelegramMediaUnavailable):
        await download_document("FID", bot=bot, max_bytes=1000)


async def test_download_rejects_oversize_after_download() -> None:
    """getFile may report no size; the downloaded bytes are still capped (defence
    in depth), so an over-ceiling file is rejected as a per-file error."""
    from app.services.telegram_media import TelegramMediaError, download_document

    bot = _Bot(getfile_result=_File(file_size=None), download_bytes=b"X" * 100)
    with pytest.raises(TelegramMediaError):
        await download_document("FID", bot=bot, max_bytes=50)
