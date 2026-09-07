"""#821 — media file validation, limits, and error handling.

* the upload cap is unified (no 6-vs-8 MB drift): one app-wide MAX_UPLOAD_BYTES
  (8 MB), matched by media.py, the receipt endpoint, and the frontend.
* sanitize rejects empty / oversize / blocked-type uploads with stable codes.
* a failed Telegram send is never silent — dual-write records ``status='failed'``
  and keeps the durable copy; telegram_only surfaces a 502 so the user retries.
"""
from __future__ import annotations

import inspect
from io import BytesIO

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import MediaFile, Tenant


def _tiny_png() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


# ── one cap, no drift (DB-free) ───────────────────────────────────────────────

def test_upload_cap_is_unified_at_8mb() -> None:
    import app.api.me as me
    from app.core.limits import MAX_UPLOAD_BYTES
    from app.services.media import MAX_IMAGE_BYTES

    assert MAX_UPLOAD_BYTES == 8 * 1024 * 1024          # matches frontend IMAGE_MAX_BYTES
    assert MAX_IMAGE_BYTES == MAX_UPLOAD_BYTES          # media.py uses the central cap
    assert not hasattr(me, "RECEIPT_MAX_BYTES")         # no divergent per-endpoint cap left
    src = inspect.getsource(me.upload_receipt)
    assert "read_capped" in src and "MAX_UPLOAD_BYTES" in src


# ── validation: empty / oversize / blocked type (DB-free) ─────────────────────

def test_sanitize_receipt_rejects_empty_oversize_blocked_type() -> None:
    from app.services.media import MAX_IMAGE_BYTES, sanitize_receipt

    with pytest.raises(HTTPException) as e_empty:
        sanitize_receipt(b"", "application/pdf")
    assert e_empty.value.status_code == 400              # empty

    big = b"%PDF-" + b"0" * (MAX_IMAGE_BYTES + 16)
    with pytest.raises(HTTPException) as e_big:
        sanitize_receipt(big, "application/pdf")
    assert e_big.value.status_code == 413                # oversize

    with pytest.raises(HTTPException) as e_type:
        sanitize_receipt(b"definitely not an image", "text/plain")
    assert e_type.value.status_code == 400               # spoofed / blocked type

    # A header fragment is NOT a valid PDF and no longer passes: the guard now
    # requires a trailer, a cross-reference and a bounded page count.
    with pytest.raises(HTTPException) as e_frag:
        sanitize_receipt(b"%PDF-1.4 minimal", "application/pdf")
    assert e_frag.value.status_code == 400

    from tests.test_kyc_media import _real_pdf
    data, ct = sanitize_receipt(_real_pdf(), "application/pdf")
    assert ct == "application/pdf" and data.startswith(b"%PDF-")   # a real PDF passes


# ── failed Telegram send is never silent (integration — test DB) ──────────────

async def test_failed_send_dual_write_records_failed_with_fallback(migrated_db, monkeypatch) -> None:
    from app.config import settings
    from app.services import media, telegram_media

    monkeypatch.setattr(settings, "media_fallback_mode", "dual_write")

    async def _boom(*a, **k):
        raise telegram_media.TelegramMediaError("send_failed")

    async def _topic(*a, **k):
        return 1

    monkeypatch.setattr(telegram_media, "send_document_to_topic", _boom)
    monkeypatch.setattr(telegram_media, "ensure_tenant_topic", _topic)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="t-821-dw", name="DW 821")
        s.add(t)
        await s.flush()
        url = await media.store_tenant_media(
            s, content=_tiny_png(), content_type="image/png",
            tenant_id=t.id, entity_type="payment_receipt",
        )
        await s.commit()
        assert "/api/media/" in url
        mf = (await s.execute(select(MediaFile).where(MediaFile.tenant_id == t.id))).scalar_one()
        assert mf.status == "failed"      # recorded, not silently swallowed
        assert mf.file_id is None         # no Telegram ref (send failed)
        assert mf.data is not None        # durable fallback kept the bytes → still servable
        assert mf.error                   # the failure reason is captured


async def test_failed_send_telegram_only_raises_502(migrated_db, monkeypatch) -> None:
    from app.config import settings
    from app.services import media, telegram_media

    monkeypatch.setattr(settings, "media_fallback_mode", "telegram_only")

    async def _boom(*a, **k):
        raise telegram_media.TelegramMediaError("send_failed")

    async def _topic(*a, **k):
        return 1

    monkeypatch.setattr(telegram_media, "send_document_to_topic", _boom)
    monkeypatch.setattr(telegram_media, "ensure_tenant_topic", _topic)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="t-821-to", name="TO 821")
        s.add(t)
        await s.flush()
        with pytest.raises(HTTPException) as ei:
            await media.store_tenant_media(
                s, content=_tiny_png(), content_type="image/png",
                tenant_id=t.id, entity_type="payment_receipt",
            )
        assert ei.value.status_code == 502   # no durable copy → upload truly failed, surfaced
