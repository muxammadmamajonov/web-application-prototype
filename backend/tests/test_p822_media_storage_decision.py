"""#822 — the storage decision is ENFORCED, not just documented.

`docs/TELEGRAM-MEDIA.md` records the decision: payment proofs + generic tenant
media use **Telegram-primary + a config-gated durable fallback**, while KYC gov-ID
PII stays **off Telegram entirely**. Because these are financial/legal records, the
guarantees must be pinned behaviourally — a source-inspection test
(`"telegram_only" in getsource(...)`) survives a logic inversion, so it can't
protect a compliance invariant.

These tests exercise ``store_tenant_media`` / ``store_kyc_image`` for real (the core
routing, ordering, status, and field-population logic runs), isolating only the two
external boundaries: the Telegram network (``send_document_to_topic`` /
``ensure_tenant_topic``) and the durable object store (``get_receipt_store``). Same
test-double style as ``test_telegram_media.FakeBot``.
"""
from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException


def _img() -> bytes:
    """A small, valid raster image (sanitize_receipt re-encodes it to WebP)."""
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (16, 16), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


class _FakeStore:
    """Durable receipt store stand-in — records every ``put`` (no I/O)."""

    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.puts.append((key, data, content_type))


class _Session:
    """Captures ``add``/``flush`` so we can assert on the persisted row in memory."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass


class _Ref:
    """A successful ``send_document_to_topic`` result."""

    message_id = 9001
    file_id = "FILEID-abc"
    file_unique_id = "UNIQ-xyz"


def _patch_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    store: _FakeStore | None,
    send,
    topic_thread: int = 1,
) -> None:
    """Isolate the two external boundaries; everything else runs for real."""
    from app.services import receipt_storage, telegram_media

    monkeypatch.setattr(receipt_storage, "get_receipt_store", lambda: store)

    async def _topic(_db, _tenant_id):
        return topic_thread

    monkeypatch.setattr(telegram_media, "ensure_tenant_topic", _topic)
    monkeypatch.setattr(telegram_media, "send_document_to_topic", send)


# ── dual_write (default): Telegram mirror + durable source-of-truth ───────────

async def test_dual_write_persists_both_refs_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings
    from app.services import media

    store = _FakeStore()

    async def _ok(_data, *, filename, thread_id, caption=None):
        return _Ref()

    _patch_boundaries(monkeypatch, store=store, send=_ok)
    monkeypatch.setattr(settings, "media_fallback_mode", "dual_write")
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)

    sess = _Session()
    url = await media.store_tenant_media(
        sess, content=_img(), content_type="image/png",
        tenant_id=uuid4(), entity_type="payment_receipt",
    )
    assert url.startswith("/api/media/")
    mf = sess.added[-1]
    assert mf.status == "stored"
    assert mf.file_id == "FILEID-abc"          # Telegram reference captured
    assert mf.object_key is not None           # AND a durable copy exists
    assert len(store.puts) == 1                 # the durable store was written


async def test_dual_write_keeps_durable_copy_when_telegram_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The core no-data-loss guarantee: a Telegram outage must NOT lose a
    financial proof — the durable copy is written first and survives."""
    from app.config import settings
    from app.services import media
    from app.services.telegram_media import TelegramMediaError

    store = _FakeStore()

    async def _boom(_data, *, filename, thread_id, caption=None):
        raise TelegramMediaError("telegram down")

    _patch_boundaries(monkeypatch, store=store, send=_boom)
    monkeypatch.setattr(settings, "media_fallback_mode", "dual_write")
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)

    sess = _Session()
    url = await media.store_tenant_media(
        sess, content=_img(), content_type="image/png",
        tenant_id=uuid4(), entity_type="payment_receipt",
    )
    # The upload still succeeds (served from the durable copy) ...
    assert url.startswith("/api/media/")
    assert len(store.puts) == 1                 # durable copy WAS written
    mf = sess.added[-1]
    # ... but the failed send is recorded honestly, and the row is recoverable.
    assert mf.status == "failed"
    assert mf.error                              # the failure cause is captured
    assert mf.file_id is None                    # no Telegram ref
    assert mf.object_key is not None             # durable copy → file not lost


async def test_dual_write_falls_back_to_postgres_when_no_object_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no private bucket configured, the durable copy lives in Postgres
    (``MediaFile.data``) — still a real source of truth, not a Telegram-only ref."""
    from app.config import settings
    from app.services import media
    from app.services.telegram_media import TelegramMediaError

    async def _boom(_data, *, filename, thread_id, caption=None):
        raise TelegramMediaError("telegram down")

    _patch_boundaries(monkeypatch, store=None, send=_boom)  # get_receipt_store() → None
    monkeypatch.setattr(settings, "media_fallback_mode", "dual_write")
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)

    sess = _Session()
    await media.store_tenant_media(
        sess, content=_img(), content_type="image/png",
        tenant_id=uuid4(), entity_type="tenant_topup",
    )
    mf = sess.added[-1]
    assert mf.status == "failed"
    assert mf.object_key is None
    assert mf.data is not None                   # durable bytes kept in Postgres


# ── telegram_only: cheapest, references only, fails loud ─────────────────────

async def test_telegram_only_keeps_reference_only_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings
    from app.services import media

    store = _FakeStore()

    async def _ok(_data, *, filename, thread_id, caption=None):
        return _Ref()

    _patch_boundaries(monkeypatch, store=store, send=_ok)
    monkeypatch.setattr(settings, "media_fallback_mode", "telegram_only")
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)

    sess = _Session()
    await media.store_tenant_media(
        sess, content=_img(), content_type="image/png",
        tenant_id=uuid4(), entity_type="payment_receipt",
    )
    mf = sess.added[-1]
    assert mf.status == "stored"
    assert mf.file_id == "FILEID-abc"
    assert mf.object_key is None and mf.data is None   # NO durable copy (cheapest)
    assert store.puts == []                             # durable store untouched


async def test_telegram_only_fails_upload_when_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """telegram_only has no durable fallback, so a failed send must fail the
    upload loudly (502) and persist nothing — the user retries, no silent loss."""
    from app.config import settings
    from app.services import media
    from app.services.telegram_media import TelegramMediaError

    store = _FakeStore()

    async def _boom(_data, *, filename, thread_id, caption=None):
        raise TelegramMediaError("telegram down")

    _patch_boundaries(monkeypatch, store=store, send=_boom)
    monkeypatch.setattr(settings, "media_fallback_mode", "telegram_only")
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)

    sess = _Session()
    with pytest.raises(HTTPException) as e:
        await media.store_tenant_media(
            sess, content=_img(), content_type="image/png",
            tenant_id=uuid4(), entity_type="payment_receipt",
        )
    assert e.value.status_code == 502
    assert e.value.detail == "media_upload_failed"
    assert store.puts == []        # never wrote a durable copy
    assert sess.added == []         # and persisted no half-baked MediaFile row


async def test_unknown_fallback_mode_fails_safe_to_dual_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo'd ``MEDIA_FALLBACK_MODE`` must fail SAFE: anything that isn't exactly
    ``telegram_only`` keeps the durable copy, so a config slip can never silently
    drop financial records to a Telegram-only (loss-prone) path."""
    from app.config import settings
    from app.services import media

    store = _FakeStore()

    async def _ok(_data, *, filename, thread_id, caption=None):
        return _Ref()

    _patch_boundaries(monkeypatch, store=store, send=_ok)
    monkeypatch.setattr(settings, "media_fallback_mode", "telegram-only")  # hyphen typo
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)

    sess = _Session()
    await media.store_tenant_media(
        sess, content=_img(), content_type="image/png",
        tenant_id=uuid4(), entity_type="payment_receipt",
    )
    mf = sess.added[-1]
    assert mf.object_key is not None      # durable copy kept → fail-safe
    assert len(store.puts) == 1


# ── KYC PII stays OFF Telegram — even with the media feature fully ON ─────────

async def test_kyc_storage_never_touches_telegram_even_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KYC gov-ID PII must stay in the private ``KycBlob`` store regardless of the
    Telegram media flags (#113/#755). Tripwires on the Telegram media path fail the
    test if KYC ever reaches it."""
    from app.config import settings
    from app.models import KycBlob
    from app.services import media, receipt_storage, telegram_media

    # Telegram media fully ON — the condition under which a regression would leak.
    monkeypatch.setattr(settings, "telegram_media_enabled", True)
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)

    async def _tripwire(*_a, **_k):
        raise AssertionError("KYC PII must NEVER use the Telegram media path")

    monkeypatch.setattr(telegram_media, "send_document_to_topic", _tripwire)
    monkeypatch.setattr(telegram_media, "ensure_tenant_topic", _tripwire)

    store = _FakeStore()
    monkeypatch.setattr(receipt_storage, "get_receipt_store", lambda: store)

    sess = _Session()
    blob_id = await media.store_kyc_image(
        sess, _img(), "image/png", tenant_id=uuid4(),
    )
    assert blob_id                                   # a KycBlob id, not a media URL
    blob = sess.added[-1]
    assert isinstance(blob, KycBlob)                 # stored as private KYC, not MediaFile
    assert blob.object_key is not None               # in the private store
    assert blob.content_type == "image/webp"         # sanitized (EXIF/GPS stripped)
    assert len(store.puts) == 1                       # the ONLY write was to the private store
