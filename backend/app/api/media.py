"""Serve tenant media stored in Telegram, through an authenticated proxy (#819).

Mirrors ``api/receipts.py``: a ``media_files`` row carries an HMAC capability that
gates a ``<img src>`` / download link. The bytes are fetched from Telegram
(``file_id``, via the platform bot) at request time and streamed — the Telegram
token, file URL, chat id, and topic id are NEVER sent to the client. If Telegram
is unavailable and a durable fallback copy exists (dual-write), that is served
instead; otherwise the request fails with ``503 media_unavailable``.
"""
from __future__ import annotations

import logging
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_media_sig
from app.db import get_db
from app.models import MediaFile

log = logging.getLogger("muddato.media")

router = APIRouter(prefix="/media", tags=["media"])


def media_url(media_id: str) -> str:
    """Relative HMAC capability URL (same-origin via the Pages proxy)."""
    from app.core.security import sign_media

    return f"/api/media/{media_id}?s={sign_media(media_id)}"


def _content_disposition(disposition: str, filename: str) -> str:
    """Build an RFC 6266 ``Content-Disposition`` that survives any filename.

    HTTP header values are latin-1, so a Cyrillic/Uzbek filename (e.g. ``чек.pdf``)
    in a bare ``filename="…"`` raises ``UnicodeEncodeError`` when the response is
    serialized — a 500 on download. We strip header-breaking / injection chars
    (quotes, backslashes, control chars), always emit an ASCII ``filename=``
    fallback, and add an RFC 5987 ``filename*=UTF-8''`` percent-encoded copy that
    modern browsers prefer — so non-ASCII names download correctly instead of
    erroring. The whole header is guaranteed latin-1 encodable."""
    clean = "".join(c for c in filename if c.isprintable() and c not in '"\\').strip() or "file"
    ascii_name = clean.encode("ascii", "ignore").decode("ascii").strip()
    if ascii_name == clean:
        return f'{disposition}; filename="{clean}"'
    # Non-ASCII present → ASCII fallback (keep the extension if that's all we have)
    # plus the exact name via RFC 5987 for browsers that support it.
    return (
        f'{disposition}; filename="{ascii_name or "file"}"; '
        f"filename*=UTF-8''{quote(clean, safe='')}"
    )


@router.get("/{media_id}")
async def serve_media(
    media_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    s: Annotated[str | None, Query()] = None,
    dl: Annotated[int, Query()] = 0,
) -> Response:
    if not verify_media_sig(str(media_id), s):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad_signature")
    # (Batch 5) A valid HMAC over this exact object is the authorization;
    # narrowing the request to the owning tenant is the reach. Unscoped,
    # this proxy would read the table with no tenant filter at all.
    from app.core.dbauth import scope_to_object

    await scope_to_object(db, "media_file", media_id)
    mf = (await db.execute(
        select(MediaFile).where(MediaFile.id == media_id)
    )).scalar_one_or_none()
    if mf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media_not_found")

    ct = mf.mime_type or "application/octet-stream"
    disposition = "attachment" if dl else "inline"
    headers = {
        "Cache-Control": "private, max-age=86400",
        "Content-Disposition": _content_disposition(
            disposition, mf.original_filename or f"file-{media_id}",
        ),
    }

    # 1) Primary: stream from Telegram (file_id, via the owning platform bot).
    if mf.file_id and mf.status == "stored":
        from app.services.telegram_media import TelegramMediaError, download_document

        try:
            data = await download_document(mf.file_id)
            return Response(content=data, media_type=ct, headers=headers)
        except TelegramMediaError as e:
            # Fall through to the durable fallback (dual-write) if we have one.
            log.warning("media.telegram_download_failed id=%s err=%s", media_id, e)

    # 2) Fallback: durable copy from the private object store …
    if mf.object_key:
        from app.services.receipt_storage import get_receipt_store

        store = get_receipt_store()
        got = await store.get(mf.object_key) if store is not None else None
        if got is not None:
            return Response(content=got[0], media_type=ct, headers=headers)

    # 3) … or the legacy Postgres blob.
    if mf.data is not None:
        return Response(content=mf.data, media_type=ct, headers=headers)

    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "media_unavailable")
