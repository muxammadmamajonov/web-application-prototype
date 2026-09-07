"""#817 — payment-proof uploads are idempotent on retry (no duplicate Telegram send).

A retried upload of the *same logical proof* (same target + amount + photo) must
reuse the existing ``media_files`` row instead of sending a second document. The key
is a server-derived digest, so no client cooperation is needed; ``store_tenant_media``
short-circuits a sequential retry before any send, and a partial-unique
``(tenant_id, idempotency_key)`` index is the concurrency backstop (a double-submit
persists at most one row).

DB-backed cases use a real ``Tenant`` row; the Telegram + topic boundaries are
monkeypatched (same style as ``test_telegram_media`` / ``test_p816``).
"""
from __future__ import annotations

import asyncio
import inspect
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker


def _img() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (16, 16), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


class _Ref:
    message_id = 7
    file_id = "FID"
    file_unique_id = "UID"


async def _make_tenant(engine, slug: str):
    from app.models import Tenant

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug=slug, name=slug.upper())
        s.add(t)
        await s.flush()
        tid = t.id
        await s.commit()
    return tid


def _mediafile(tenant_id, *, idempotency_key):
    from app.models import MediaFile

    return MediaFile(
        id=uuid4(), tenant_id=tenant_id, bot_owner="platform", status="stored",
        mime_type="image/webp", idempotency_key=idempotency_key,
    )


def _enable_telegram(monkeypatch, *, sends: dict):
    from app.config import settings
    from app.services import telegram_media

    monkeypatch.setattr(settings, "telegram_media_enabled", True)
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)
    monkeypatch.setattr(settings, "media_fallback_mode", "telegram_only")

    async def _topic(_db, _tid, **_k):
        return 1

    async def _send(_data, *, filename, thread_id, caption=None):
        sends["n"] += 1
        return _Ref()

    monkeypatch.setattr(telegram_media, "ensure_tenant_topic", _topic)
    monkeypatch.setattr(telegram_media, "send_document_to_topic", _send)


async def _row_count(engine, tenant_id) -> int:
    from app.models import MediaFile

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        return (await s.execute(
            select(func.count()).select_from(MediaFile).where(MediaFile.tenant_id == tenant_id)
        )).scalar_one()


# ── key derivation (DB-free) ─────────────────────────────────────────────────

def test_idempotency_key_is_stable_and_discriminating() -> None:
    from app.services.media import payment_proof_idempotency_key

    k = payment_proof_idempotency_key(entity_type="payment_receipt", discriminator="i1|100", content=b"PHOTO")
    assert k == payment_proof_idempotency_key(entity_type="payment_receipt", discriminator="i1|100", content=b"PHOTO")
    assert len(k) == 64   # sha256 hex
    # any change to target, amount, photo, or entity type → a different upload
    assert k != payment_proof_idempotency_key(entity_type="payment_receipt", discriminator="i1|200", content=b"PHOTO")
    assert k != payment_proof_idempotency_key(entity_type="payment_receipt", discriminator="i1|100", content=b"OTHER")
    assert k != payment_proof_idempotency_key(entity_type="tenant_topup", discriminator="i1|100", content=b"PHOTO")


async def test_store_payment_proof_derives_and_passes_key(monkeypatch) -> None:
    from app.config import settings
    from app.services import media

    monkeypatch.setattr(settings, "telegram_media_enabled", True)
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)

    captured: dict = {}

    async def _fake_stm(db, *, content, content_type, tenant_id, entity_type,
                        uploaded_by=None, idempotency_key=None):
        captured["key"] = idempotency_key
        return "/api/media/x?s=1"

    monkeypatch.setattr(media, "store_tenant_media", _fake_stm)

    await media.store_payment_proof(
        None, b"PHOTO", "image/png", tenant_id=uuid4(),
        entity_type="payment_receipt", idempotency_discriminator="i1|100",
    )
    assert captured["key"] == media.payment_proof_idempotency_key(
        entity_type="payment_receipt", discriminator="i1|100", content=b"PHOTO",
    )

    # no discriminator → no key (key-less generic media still works)
    await media.store_payment_proof(
        None, b"PHOTO", "image/png", tenant_id=uuid4(), entity_type="payment_receipt",
    )
    assert captured["key"] is None


def test_both_upload_endpoints_pass_a_discriminator() -> None:
    import app.api.me as me
    import app.api.tenant as tenant

    assert "idempotency_discriminator" in inspect.getsource(me.upload_receipt)
    assert "idempotency_discriminator" in inspect.getsource(tenant.tenant_create_topup)


# ── dedup behaviour (real PG) ────────────────────────────────────────────────

async def test_sequential_retry_reuses_row_without_resending(migrated_db, monkeypatch) -> None:
    from app.services import media

    sends = {"n": 0}
    _enable_telegram(monkeypatch, sends=sends)
    tid = await _make_tenant(migrated_db, "idem-seq")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async def _store():
        async with Session() as s:
            url = await media.store_tenant_media(
                s, content=_img(), content_type="image/png",
                tenant_id=tid, entity_type="payment_receipt", idempotency_key="k-seq",
            )
            await s.commit()
            return url

    url1 = await _store()
    url2 = await _store()                 # the retry
    assert url1 == url2                    # same proxy URL
    assert sends["n"] == 1                 # the retry did NOT re-send
    assert await _row_count(migrated_db, tid) == 1


async def test_distinct_keys_create_distinct_rows(migrated_db, monkeypatch) -> None:
    from app.services import media

    sends = {"n": 0}
    _enable_telegram(monkeypatch, sends=sends)
    tid = await _make_tenant(migrated_db, "idem-distinct")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    for key in ("k1", "k2"):
        async with Session() as s:
            await media.store_tenant_media(
                s, content=_img(), content_type="image/png",
                tenant_id=tid, entity_type="payment_receipt", idempotency_key=key,
            )
            await s.commit()
    assert sends["n"] == 2
    assert await _row_count(migrated_db, tid) == 2


async def test_no_key_does_not_dedup(migrated_db, monkeypatch) -> None:
    """Key-less generic media keeps the original behaviour — every call stores a row."""
    from app.services import media

    sends = {"n": 0}
    _enable_telegram(monkeypatch, sends=sends)
    tid = await _make_tenant(migrated_db, "idem-nokey")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    for _ in range(2):
        async with Session() as s:
            await media.store_tenant_media(
                s, content=_img(), content_type="image/png",
                tenant_id=tid, entity_type="tenant_media",  # idempotency_key=None
            )
            await s.commit()
    assert sends["n"] == 2
    assert await _row_count(migrated_db, tid) == 2


async def test_concurrent_double_submit_persists_one_row(migrated_db, monkeypatch) -> None:
    from app.services import media

    sends = {"n": 0}
    _enable_telegram(monkeypatch, sends=sends)
    tid = await _make_tenant(migrated_db, "idem-conc")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async def worker():
        async with Session() as s:
            url = await media.store_tenant_media(
                s, content=_img(), content_type="image/png",
                tenant_id=tid, entity_type="payment_receipt", idempotency_key="k-race",
            )
            await s.commit()
            return url

    u1, u2 = await asyncio.gather(worker(), worker())
    # The unique (tenant, key) index guarantees at most one persisted row even when
    # both requests race past the pre-check; both callers resolve to it.
    assert await _row_count(migrated_db, tid) == 1
    assert u1 == u2
    assert u1.startswith("/api/media/")


async def test_competitor_winning_between_precheck_and_insert_is_reused(migrated_db, monkeypatch) -> None:
    """Deterministically exercise the unique-index backstop: a competitor row wins
    the key AFTER our pre-check passes but BEFORE our insert. The begin_nested
    IntegrityError catch must reuse the competitor's row, not raise or double-store."""
    from app.services import media, telegram_media

    sends = {"n": 0}
    _enable_telegram(monkeypatch, sends=sends)
    tid = await _make_tenant(migrated_db, "idem-mid")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async def _send_then_competitor(_data, *, filename, thread_id, caption=None):
        sends["n"] += 1
        # A separate committed transaction grabs the key while we're "sending".
        async with Session() as s2:
            s2.add(_mediafile(tid, idempotency_key="k-mid"))
            await s2.commit()
        return _Ref()

    monkeypatch.setattr(telegram_media, "send_document_to_topic", _send_then_competitor)

    async with Session() as s:
        url = await media.store_tenant_media(
            s, content=_img(), content_type="image/png",
            tenant_id=tid, entity_type="payment_receipt", idempotency_key="k-mid",
        )
        await s.commit()
    assert url.startswith("/api/media/")
    assert await _row_count(migrated_db, tid) == 1   # only the competitor's row persisted


async def test_partial_unique_index_enforced(migrated_db) -> None:
    a = await _make_tenant(migrated_db, "idem-uq-a")
    b = await _make_tenant(migrated_db, "idem-uq-b")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    # same (tenant, key) → rejected
    with pytest.raises(IntegrityError):
        async with Session() as s:
            s.add(_mediafile(a, idempotency_key="dup"))
            s.add(_mediafile(a, idempotency_key="dup"))
            await s.commit()

    # different tenant, same key → fine (scope is per-tenant)
    async with Session() as s:
        s.add(_mediafile(a, idempotency_key="shared"))
        s.add(_mediafile(b, idempotency_key="shared"))
        await s.commit()

    # same tenant, NULL key twice → fine (partial index excludes NULLs)
    async with Session() as s:
        s.add(_mediafile(a, idempotency_key=None))
        s.add(_mediafile(a, idempotency_key=None))
        await s.commit()

    # tenant a now holds "shared" + two NULL-key rows (the dup attempt rolled back)
    assert await _row_count(migrated_db, a) == 3
