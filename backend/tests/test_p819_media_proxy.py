"""#819 — the ``/api/media`` proxy is the single authz chokepoint; test it hard.

``serve_media`` is the only path by which a stored tenant file reaches a client, so
it must:

* reject a missing/forged HMAC capability with **403 before any DB access**,
* **404** an unknown id (only after the capability check — no existence oracle),
* stream **inline** for preview and **attachment** for download,
* fetch bytes **server-side** from Telegram and, when Telegram is down, fall back to
  the durable copy (object store → Postgres), else **503 media_unavailable**,
* never leak Telegram internals (token, ``file_path``, chat id, topic id, file_id),
* and download a **non-ASCII filename** without 500ing on the latin-1 header.

DB-backed cases use a real ``MediaFile`` row — platform media (``tenant_id`` NULL,
no FK needed). The Telegram + durable-store boundaries are monkeypatched, the same
test-double style as ``test_telegram_media`` / ``test_p822``.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.media import _content_disposition, serve_media
from app.core.security import sign_media


def _sig(mid: UUID) -> str:
    return sign_media(str(mid))


def _returns(value: bytes):
    """An async ``download_document`` stand-in that yields fixed bytes."""
    async def _dl(*_a, **_k):
        return value
    return _dl


async def _insert_media(engine, *, mid: UUID, **fields) -> None:
    from app.models import MediaFile

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as sess:
        sess.add(MediaFile(id=mid, tenant_id=None, bot_owner="platform", **fields))
        await sess.commit()


# ── HMAC capability gate — DB-free (the sig check precedes any DB access) ─────

async def test_missing_signature_is_forbidden_without_touching_db() -> None:
    # db=None proves the 403 is raised before any DB access.
    with pytest.raises(HTTPException) as e:
        await serve_media(uuid4(), db=None, s=None)  # type: ignore[arg-type]
    assert e.value.status_code == 403


async def test_capability_for_one_media_cannot_open_another() -> None:
    target = uuid4()
    with pytest.raises(HTTPException) as e:
        # a valid signature, but minted for a DIFFERENT media id
        await serve_media(target, db=None, s=_sig(uuid4()))  # type: ignore[arg-type]
    assert e.value.status_code == 403


# ── Content-Disposition builder — DB-free unit coverage ──────────────────────

def test_content_disposition_ascii_is_plain() -> None:
    assert _content_disposition("attachment", "receipt.pdf") == 'attachment; filename="receipt.pdf"'


def test_content_disposition_unicode_uses_rfc5987_and_is_latin1_safe() -> None:
    cd = _content_disposition("inline", "чек.pdf")
    assert "filename*=UTF-8''" in cd
    assert "%D1%87" in cd            # 'ч' percent-encoded
    assert "чек" not in cd           # the raw Cyrillic is NOT in the header
    cd.encode("latin-1")             # must not raise — this is the bug we fixed


def test_content_disposition_strips_quotes_and_control_chars() -> None:
    cd = _content_disposition("attachment", 'a"b\n.pdf')
    cd.encode("latin-1")
    assert "\n" not in cd
    assert cd.count('"') == 2        # only the two structural quotes survive


def test_content_disposition_empty_after_cleaning_falls_back() -> None:
    assert _content_disposition("inline", '"\n\t"') == 'inline; filename="file"'


# ── streaming: Telegram primary, inline vs attachment, no leaks ──────────────

async def test_serves_from_telegram_when_stored(migrated_db, monkeypatch) -> None:
    from app.services import telegram_media

    mid = uuid4()
    await _insert_media(
        migrated_db, mid=mid, status="stored", file_id="TGFILE-secret",
        mime_type="image/webp", original_filename="r.webp",
    )

    async def _dl(file_id, **_k):
        assert file_id == "TGFILE-secret"        # fetched by file_id, server-side
        return b"TELEGRAM-BYTES"

    monkeypatch.setattr(telegram_media, "download_document", _dl)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as sess:
        resp = await serve_media(mid, db=sess, s=_sig(mid), dl=0)

    assert resp.status_code == 200
    assert resp.body == b"TELEGRAM-BYTES"
    assert resp.media_type == "image/webp"
    assert resp.headers["content-disposition"].startswith("inline")
    assert resp.headers["cache-control"] == "private, max-age=86400"
    # No Telegram internals leak through any header.
    joined = " ".join(resp.headers.values())
    assert "TGFILE-secret" not in joined
    assert "file_path" not in joined


async def test_download_query_sets_attachment(migrated_db, monkeypatch) -> None:
    from app.services import telegram_media

    mid = uuid4()
    await _insert_media(
        migrated_db, mid=mid, status="stored", file_id="F",
        mime_type="application/pdf", original_filename="slip.pdf",
    )
    monkeypatch.setattr(telegram_media, "download_document", _returns(b"x"))

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as sess:
        resp = await serve_media(mid, db=sess, s=_sig(mid), dl=1)
    assert resp.headers["content-disposition"].startswith("attachment")


# ── fallback chain: Telegram down → object store → Postgres → 503 ────────────

async def test_falls_back_to_object_store_when_telegram_fails(migrated_db, monkeypatch) -> None:
    from app.services import receipt_storage, telegram_media

    mid = uuid4()
    await _insert_media(
        migrated_db, mid=mid, status="stored", file_id="F",
        object_key="media/x.webp", mime_type="image/webp", original_filename="r.webp",
    )

    async def _boom(*_a, **_k):
        raise telegram_media.TelegramMediaError("telegram down")

    monkeypatch.setattr(telegram_media, "download_document", _boom)

    class _Store:
        async def get(self, key):
            assert key == "media/x.webp"
            return (b"DURABLE-BYTES", "image/webp")

    monkeypatch.setattr(receipt_storage, "get_receipt_store", lambda: _Store())

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as sess:
        resp = await serve_media(mid, db=sess, s=_sig(mid))
    assert resp.body == b"DURABLE-BYTES"


async def test_falls_back_to_postgres_data_when_no_object_store(migrated_db) -> None:
    mid = uuid4()
    # status='failed' + file_id NULL → the Telegram branch is skipped entirely;
    # object_key NULL → no object store; the durable bytes live in Postgres.
    await _insert_media(
        migrated_db, mid=mid, status="failed", file_id=None,
        data=b"PG-BYTES", mime_type="application/pdf", original_filename="slip.pdf",
    )
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as sess:
        resp = await serve_media(mid, db=sess, s=_sig(mid))
    assert resp.body == b"PG-BYTES"


async def test_nothing_servable_returns_503(migrated_db) -> None:
    mid = uuid4()
    await _insert_media(
        migrated_db, mid=mid, status="failed", file_id=None,
        object_key=None, data=None, mime_type="image/webp",
    )
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as sess:
        with pytest.raises(HTTPException) as e:
            await serve_media(mid, db=sess, s=_sig(mid))
    assert e.value.status_code == 503
    assert e.value.detail == "media_unavailable"


async def test_unknown_media_returns_404_after_valid_signature(migrated_db) -> None:
    mid = uuid4()  # never inserted, but the signature is valid
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as sess:
        with pytest.raises(HTTPException) as e:
            await serve_media(mid, db=sess, s=_sig(mid))
    assert e.value.status_code == 404


# ── the bug fix end-to-end: a Cyrillic filename downloads, doesn't 500 ───────

async def test_cyrillic_filename_downloads_without_500(migrated_db, monkeypatch) -> None:
    from app.services import telegram_media

    mid = uuid4()
    await _insert_media(
        migrated_db, mid=mid, status="stored", file_id="F",
        mime_type="application/pdf", original_filename="чек-оплаты.pdf",
    )
    monkeypatch.setattr(telegram_media, "download_document", _returns(b"%PDF-"))

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as sess:
        # Before the fix, building this Response raised UnicodeEncodeError → 500.
        resp = await serve_media(mid, db=sess, s=_sig(mid), dl=1)
    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    assert cd.startswith("attachment")
    assert "filename*=UTF-8''%D1%87" in cd     # the real name, percent-encoded
    cd.encode("latin-1")                        # latin-1 safe → no 500
