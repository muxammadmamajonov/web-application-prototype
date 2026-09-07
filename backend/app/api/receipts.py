"""Serve customer-uploaded receipt images.

The receipt bytes live in ``receipt_blobs``; ``payments.receipt_image_url``
stores a short, HMAC-signed capability URL pointing here. Access is gated by
the signature (not the bearer token) because the URL is rendered directly via
``<img src>`` / ``<a href>``, which can't send the Authorization header. The
signed URL is only ever returned inside authenticated API responses.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_receipt_sig
from app.db import get_db
from app.models import ReceiptBlob

router = APIRouter(prefix="/receipts", tags=["receipts"])


def receipt_url(blob_id: str) -> str:
    """Relative capability URL (same-origin via the Pages proxy)."""
    from app.core.security import sign_receipt
    return f"/api/receipts/{blob_id}?s={sign_receipt(blob_id)}"


@router.get("/{blob_id}")
async def serve_receipt(
    blob_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    s: Annotated[str | None, Query()] = None,
) -> Response:
    if not verify_receipt_sig(str(blob_id), s):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad_signature")
    # (Batch 5) A valid HMAC over this exact object is the authorization;
    # narrowing the request to the owning tenant is the reach. Unscoped,
    # this proxy would read the table with no tenant filter at all.
    from app.core.dbauth import scope_to_object

    await scope_to_object(db, "receipt_blob", blob_id)
    blob = (await db.execute(
        select(ReceiptBlob).where(ReceiptBlob.id == blob_id)
    )).scalar_one_or_none()
    if blob is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "receipt_not_found")

    headers = {"Cache-Control": "private, max-age=86400"}
    # (#96) Object-stored receipt: fetch the bytes from the private bucket and
    # proxy them (the bucket is never exposed; access stays gated by the HMAC sig).
    if blob.object_key:
        from app.services.receipt_storage import get_receipt_store
        store = get_receipt_store()
        if store is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "receipt_store_unavailable")
        got = await store.get(blob.object_key)
        if got is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "receipt_not_found")
        data, ct = got
        return Response(content=data, media_type=ct, headers=headers)

    # Legacy Postgres blob.
    if blob.data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "receipt_not_found")
    return Response(
        content=blob.data,
        media_type=blob.content_type or "image/jpeg",
        headers=headers,
    )
