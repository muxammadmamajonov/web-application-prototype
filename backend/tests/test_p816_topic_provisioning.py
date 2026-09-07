"""#816 — per-tenant Telegram topic provisioning is idempotent + self-healing.

Two acceptance criteria need real enforcement:

* **Idempotent / concurrency-safe** — concurrent or retried first-uploads must never
  create duplicate topics. ``ensure_tenant_topic`` locks the tenant row ``FOR UPDATE``
  so the loser of a race blocks, then returns the winner's persisted id.
* **Self-healing** — a topic deleted on Telegram's side is detected at send time
  (``TelegramTopicMissing``) and recreated once, so an admin removing a topic can't
  permanently wedge a tenant's uploads.

Platform-owner media (``tenant_id is None``) resolves to the General topic (1) with
no API call. The Telegram + durable-store boundaries are monkeypatched (same style as
``test_telegram_media`` / ``test_p822``); DB-backed cases use a real ``Tenant`` row.
"""
from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


def _img() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (16, 16), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


class _FakeApiError(TelegramAPIError):
    """A Telegram API error with a controllable description (real ones need a
    TelegramMethod instance to construct)."""

    def __init__(self, message: str) -> None:
        Exception.__init__(self, message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class _FailBot:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def send_document(self, chat, *, document=None, message_thread_id=None, caption=None):
        raise self._exc


class _Ref:
    message_id = 555
    file_id = "FID"
    file_unique_id = "UID"


# ── missing-topic classification (DB-free) ───────────────────────────────────

def test_is_missing_topic_error_matches_known_phrases() -> None:
    from app.services.telegram_media import _is_missing_topic_error

    assert _is_missing_topic_error(Exception("Bad Request: message thread not found"))
    assert _is_missing_topic_error(Exception("TOPIC_DELETED"))
    assert _is_missing_topic_error(Exception("topic_closed"))
    # unrelated failures must NOT be mistaken for a gone topic
    assert not _is_missing_topic_error(Exception("Bad Request: chat not found"))
    assert not _is_missing_topic_error(Exception("Forbidden: bot was blocked by the user"))


async def test_send_raises_topic_missing_on_deleted_thread(monkeypatch) -> None:
    from app.core import breaker
    from app.services.telegram_media import TelegramTopicMissing, send_document_to_topic

    monkeypatch.setattr(breaker.telegram_breaker, "allow", lambda: True)
    bot = _FailBot(_FakeApiError("Bad Request: message thread not found"))
    with pytest.raises(TelegramTopicMissing):
        await send_document_to_topic(b"x", filename="f.pdf", thread_id=99, chat_id=-100, bot=bot)


async def test_send_raises_generic_error_on_other_api_failure(monkeypatch) -> None:
    from app.core import breaker
    from app.services.telegram_media import (
        TelegramMediaError,
        TelegramTopicMissing,
        send_document_to_topic,
    )

    monkeypatch.setattr(breaker.telegram_breaker, "allow", lambda: True)
    bot = _FailBot(_FakeApiError("Bad Request: chat not found"))
    with pytest.raises(TelegramMediaError) as e:
        await send_document_to_topic(b"x", filename="f.pdf", thread_id=99, chat_id=-100, bot=bot)
    assert not isinstance(e.value, TelegramTopicMissing)   # not misclassified as a gone topic


# ── platform General topic — no API call (DB-free) ───────────────────────────

async def test_platform_media_resolves_to_general_without_api(monkeypatch) -> None:
    from app.services import telegram_media

    async def _tripwire(*_a, **_k):
        raise AssertionError("platform General topic must not hit the Telegram API")

    monkeypatch.setattr(telegram_media, "create_topic", _tripwire)
    assert await telegram_media.ensure_tenant_topic(db=None, tenant_id=None) == 1


# ── idempotent provisioning (real PG) ────────────────────────────────────────

async def _make_tenant(engine, *, slug: str, **fields):
    from app.models import Tenant

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug=slug, name=slug.upper(), **fields)
        s.add(t)
        await s.flush()
        tid = t.id
        await s.commit()
    return tid


async def test_provisions_topic_once_then_returns_cached(migrated_db, monkeypatch) -> None:
    from app.services import telegram_media

    calls = {"n": 0}

    async def _create(_name, **_k):
        calls["n"] += 1
        return 4242

    monkeypatch.setattr(telegram_media, "create_topic", _create)
    tid = await _make_tenant(migrated_db, slug="topic-a")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        r1 = await telegram_media.ensure_tenant_topic(s, tid)
        await s.commit()
    async with Session() as s:
        r2 = await telegram_media.ensure_tenant_topic(s, tid)   # cached → no new API call
        await s.commit()

    assert r1 == 4242 and r2 == 4242
    assert calls["n"] == 1


async def test_concurrent_provisioning_creates_exactly_one_topic(migrated_db, monkeypatch) -> None:
    """The race the FOR UPDATE lock closes: two simultaneous first-uploads must
    create exactly one topic, not two (the second would orphan a topic)."""
    from app.services import telegram_media

    calls = {"n": 0}

    async def _create(_name, **_k):
        calls["n"] += 1
        await asyncio.sleep(0.05)        # widen the window so both workers contend
        return 5000 + calls["n"]         # distinct ids → a double-create is visible

    monkeypatch.setattr(telegram_media, "create_topic", _create)
    tid = await _make_tenant(migrated_db, slug="topic-c")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async def worker():
        async with Session() as s:
            r = await telegram_media.ensure_tenant_topic(s, tid)
            await s.commit()
            return r

    r1, r2 = await asyncio.gather(worker(), worker())
    assert calls["n"] == 1               # exactly one topic created despite concurrency
    assert r1 == r2 == 5001              # both resolve to the same persisted id


async def test_force_recreate_provisions_a_new_topic(migrated_db, monkeypatch) -> None:
    from app.models import Tenant
    from app.services import telegram_media

    seq = {"n": 100}

    async def _create(_name, **_k):
        seq["n"] += 1
        return seq["n"]

    monkeypatch.setattr(telegram_media, "create_topic", _create)
    tid = await _make_tenant(migrated_db, slug="topic-r", telegram_topic_id=100)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        r = await telegram_media.ensure_tenant_topic(s, tid, force_recreate=True)
        await s.commit()
    assert r == 101                       # a fresh topic, not the stale 100

    async with Session() as s:
        t = await s.get(Tenant, tid)
        assert t.telegram_topic_id == 101  # the new id is persisted


# ── self-healing: store_tenant_media recreates a deleted topic (real PG) ──────

async def test_store_tenant_media_recreates_topic_on_missing(migrated_db, monkeypatch) -> None:
    from app.config import settings
    from app.models import MediaFile
    from app.services import media, telegram_media

    monkeypatch.setattr(settings, "telegram_media_enabled", True)
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)
    monkeypatch.setattr(settings, "media_fallback_mode", "telegram_only")  # isolate the TG path

    tid = await _make_tenant(migrated_db, slug="topic-x", telegram_topic_id=777)

    async def _create(_name, **_k):
        return 888

    sends = {"n": 0}

    async def _send(_data, *, filename, thread_id, caption=None):
        sends["n"] += 1
        if sends["n"] == 1:
            assert thread_id == 777        # first send targets the stale topic
            raise telegram_media.TelegramTopicMissing("topic_missing")
        assert thread_id == 888            # retry targets the recreated topic
        return _Ref()

    monkeypatch.setattr(telegram_media, "create_topic", _create)
    monkeypatch.setattr(telegram_media, "send_document_to_topic", _send)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        url = await media.store_tenant_media(
            s, content=_img(), content_type="image/png",
            tenant_id=tid, entity_type="payment_receipt",
        )
        await s.commit()

    assert url.startswith("/api/media/")
    assert sends["n"] == 2                 # recreated + retried exactly once

    async with Session() as s:
        mf = (await s.execute(select(MediaFile).where(MediaFile.tenant_id == tid))).scalar_one()
        assert mf.status == "stored"
        assert mf.message_thread_id == 888  # points at the recreated topic
