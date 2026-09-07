"""Telegram-group media storage (#814) — DB-free unit coverage.

Exercises the send/download/topic primitives with a fake aiogram Bot (no network),
the size-ceiling enforcement, the HMAC media capability, config defaults, the
migration, and the upload-path / proxy wiring (via source inspection, the same
style as test_p2_receipt_storage.py).
"""
from __future__ import annotations

import inspect
import io
from pathlib import Path

import pytest

_MIG = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "r85tgmedia_telegram_media_files.py"
)


# ── fake aiogram Bot (no network) ────────────────────────────────────────────

class _Doc:
    file_id = "FILEID-abc"
    file_unique_id = "UNIQ-xyz"
    file_name = "receipt.pdf"
    mime_type = "application/pdf"
    file_size = 1234


class _Msg:
    message_id = 9001
    document = _Doc()


class _File:
    def __init__(self, *, file_size: int = 1234, file_path: str = "documents/file_1.pdf") -> None:
        self.file_size = file_size
        self.file_path = file_path


class _Topic:
    def __init__(self, thread_id: int = 4242) -> None:
        self.message_thread_id = thread_id


class FakeBot:
    """Minimal stand-in for aiogram.Bot — records calls, returns canned objects."""

    def __init__(self, *, download_bytes: bytes = b"PDFBYTES", file_size: int = 1234) -> None:
        self.sent: list[dict] = []
        self.created: list[tuple] = []
        self._download = download_bytes
        self._file_size = file_size

    async def send_document(self, chat, *, document=None, message_thread_id=None, caption=None):
        self.sent.append({
            "chat": chat, "thread": message_thread_id,
            "filename": getattr(document, "filename", None), "caption": caption,
        })
        return _Msg()

    async def get_file(self, file_id):
        return _File(file_size=self._file_size)

    async def download_file(self, file_path):
        return io.BytesIO(self._download)

    async def create_forum_topic(self, chat, *, name):
        self.created.append((chat, name))
        return _Topic()


# ── config defaults ──────────────────────────────────────────────────────────

def test_config_media_defaults() -> None:
    from app.config import settings

    # OFF by default → the existing receipt path stays unchanged.
    assert settings.telegram_media_enabled is False
    assert settings.telegram_media_chat_id is None
    # 20 MB getFile ceiling, and dual-write is the safe default.
    assert settings.telegram_media_max_bytes == 20 * 1024 * 1024
    assert settings.media_fallback_mode == "dual_write"


# ── send_document_to_topic ───────────────────────────────────────────────────

async def test_send_document_to_topic_returns_ref_and_passes_thread() -> None:
    from app.services.telegram_media import send_document_to_topic

    bot = FakeBot()
    ref = await send_document_to_topic(
        b"BYTES", filename="receipt.pdf", thread_id=7, chat_id=-1001234567891, bot=bot,
    )
    assert ref.file_id == "FILEID-abc"
    assert ref.file_unique_id == "UNIQ-xyz"
    assert ref.message_id == 9001
    # the document went to the right group + topic
    assert bot.sent[-1]["chat"] == -1001234567891
    assert bot.sent[-1]["thread"] == 7
    # …with the sniffable extension stripped. This assertion used to require the
    # ORIGINAL name, which is what broke the feature in production: Telegram
    # classifies an upload by its filename extension, and `sanitize_image`
    # re-encodes every receipt to WebP — which Telegram ingests as a STICKER, so
    # `message.document` comes back empty and the send fails with
    # `no_document_in_response`. Verified against the live group: identical bytes
    # named `.webp` returned sticker=True/document=False, named `.bin` returned
    # document=True. The real content type lives on the media_files row and the
    # download proxy serves from that, so the Telegram-side name is cosmetic.
    assert bot.sent[-1]["filename"] == "receipt.bin"


async def test_general_topic_is_addressed_by_omitting_the_thread_id() -> None:
    """Platform/subscription receipts route to General, which has no thread id.

    Passing General's nominal id (1) is rejected by Telegram with "message thread
    not found" — confirmed against the live group — so every platform-level
    receipt would have failed. General is addressed by sending no thread at all.
    """
    from app.services.telegram_media import GENERAL_TOPIC_ID, send_document_to_topic

    bot = FakeBot()
    await send_document_to_topic(
        b"BYTES", filename="topup.webp", thread_id=GENERAL_TOPIC_ID,
        chat_id=-1001234567891, bot=bot,
    )
    assert bot.sent[-1]["thread"] is None, "General must be addressed with no thread id"


async def test_a_real_topic_still_receives_its_thread_id() -> None:
    """Guards the fix above from over-reaching: only General is special-cased."""
    from app.services.telegram_media import send_document_to_topic

    bot = FakeBot()
    await send_document_to_topic(
        b"BYTES", filename="proof.webp", thread_id=42,
        chat_id=-1001234567891, bot=bot,
    )
    assert bot.sent[-1]["thread"] == 42


async def test_send_blocked_when_breaker_open(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import breaker
    from app.services.telegram_media import TelegramMediaUnavailable, send_document_to_topic

    monkeypatch.setattr(breaker.telegram_breaker, "allow", lambda: False)
    with pytest.raises(TelegramMediaUnavailable):
        await send_document_to_topic(b"x", filename="f.pdf", thread_id=1, chat_id=-100, bot=FakeBot())


# ── download_document (20 MB ceiling) ─────────────────────────────────────────

async def test_download_returns_bytes() -> None:
    from app.services.telegram_media import download_document

    data = await download_document("FILEID-abc", bot=FakeBot(download_bytes=b"HELLO"), max_bytes=1_000_000)
    assert data == b"HELLO"


async def test_download_rejects_oversize_via_get_file_size() -> None:
    from app.services.telegram_media import TelegramMediaError, download_document

    # get_file reports a size over the cap → rejected before downloading bytes.
    bot = FakeBot(file_size=30 * 1024 * 1024)
    with pytest.raises(TelegramMediaError):
        await download_document("FILEID-abc", bot=bot, max_bytes=20 * 1024 * 1024)


# ── ensure_tenant_topic platform branch (DB-free) ────────────────────────────

async def test_ensure_tenant_topic_platform_is_general() -> None:
    from app.services.telegram_media import GENERAL_TOPIC_ID, ensure_tenant_topic

    # tenant_id None == platform-owner media → General topic (id 1), no DB / API call.
    assert await ensure_tenant_topic(db=None, tenant_id=None) == GENERAL_TOPIC_ID == 1


async def test_create_topic_returns_thread_id() -> None:
    from app.services.telegram_media import create_topic

    bot = FakeBot()
    tid = await create_topic("Acme · 1234abcd", chat_id=-100, bot=bot)
    assert tid == 4242
    assert bot.created[-1][0] == -100


# ── HMAC media capability ────────────────────────────────────────────────────

def test_media_signature_roundtrip_and_rejects_tampering() -> None:
    from app.core.security import sign_media, verify_media_sig

    mid = "11111111-1111-1111-1111-111111111111"
    sig = sign_media(mid)
    assert verify_media_sig(mid, sig) is True
    assert verify_media_sig(mid, "deadbeef") is False
    assert verify_media_sig(mid, None) is False
    # a receipt signature must NOT validate a media id (separate keyspaces)
    from app.core.security import sign_receipt

    assert verify_media_sig(mid, sign_receipt(mid)) is False


def test_media_url_shape() -> None:
    from app.api.media import media_url

    url = media_url("abc-123")
    assert url.startswith("/api/media/abc-123?s=")


# ── proxy wiring (source inspection — never leaks Telegram internals) ─────────

def test_serve_media_streams_telegram_then_falls_back() -> None:
    import app.api.media as m

    src = inspect.getsource(m.serve_media)
    assert "verify_media_sig" in src                 # HMAC-gated
    assert "download_document" in src                # primary = Telegram
    assert "object_key" in src and "mf.data" in src  # durable fallback
    assert "media_unavailable" in src                # graceful 503
    # the Telegram file path / token is fetched inside aiogram, never returned
    assert "file_path" not in src


# ── upload path gating (#820) ────────────────────────────────────────────────

def test_store_receipt_routes_to_telegram_only_when_enabled() -> None:
    import app.api.me as me
    from app.services import media

    # (#820) The flag-based routing now lives in the shared payment-proof gate.
    gate = inspect.getsource(media.store_payment_proof)
    assert "telegram_media_enabled" in gate  # flag ON → new media path
    assert "store_tenant_media" in gate
    assert "store_receipt" in gate           # flag OFF (default) → legacy store
    # and the customer-receipt path delegates to that gate
    assert "store_payment_proof" in inspect.getsource(me._store_receipt)


def test_store_tenant_media_sanitizes_and_dual_writes() -> None:
    from app.services import media

    src = inspect.getsource(media.store_tenant_media)
    assert "sanitize_receipt" in src                 # validate + re-encode first
    assert "send_document_to_topic" in src           # Telegram send
    assert "ensure_tenant_topic" in src              # per-tenant topic
    assert "get_receipt_store" in src                # durable fallback (dual-write)
    assert "telegram_only" in src                    # honours the fallback mode


# ── migration r85 ────────────────────────────────────────────────────────────

def test_migration_creates_media_files_with_rls_and_topic_column() -> None:
    src = _MIG.read_text(encoding="utf-8")
    assert 'revision = "r85tgmedia"' in src
    assert 'down_revision = "r84webhookdlq"' in src
    assert "create_table" in src and '"media_files"' in src
    assert "telegram_topic_id" in src                # tenants column
    assert "file_id" in src and "file_unique_id" in src and "object_key" in src
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "tenant_isolation" in src
