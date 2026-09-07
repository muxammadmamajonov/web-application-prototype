"""Image hosting via archive.org (IAS3).

Uploads an image and returns its permanent public URL. The S3 secret lives only
in settings (a Fly secret) — never reaches any client. Used by the platform
catalog (brand logos / category & model images) and tenant product photos.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets as pysecrets
from io import BytesIO
from uuid import UUID

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.limits import MAX_UPLOAD_BYTES
from app.core.retry import retry_http

log = logging.getLogger("muddato.media")

# (#821) The single app-wide upload cap (8 MB) — matched by the receipt endpoint
# and the frontend (lib/image IMAGE_MAX_BYTES). Raster images are compressed down
# after this gate.
MAX_IMAGE_BYTES = MAX_UPLOAD_BYTES
# Longest-side cap. Raster images are downscaled to fit this box (aspect ratio
# preserved — never upscaled, never cropped) so logos/photos stay sharp.
MAX_DIM = 512
WEBP_QUALITY = 82


#: Formats a PAYMENT PROOF may be. Narrower than what Pillow can decode: every
#: accepted format is a decoder we are exposed to, and nobody photographs a bank
#: slip into a TIFF. Product/KYC images keep the wider set (``formats=None``),
#: since those are re-encoded too and the surface is a shop's own catalogue.
RECEIPT_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


def sanitize_image(
    data: bytes,
    content_type: str | None = None,
    *,
    formats: frozenset[str] | None = None,
) -> tuple[bytes, str]:
    """Validate + sanitize an uploaded raster image (#114).

    Sniffs that the bytes are a real raster image (Pillow ``verify`` rejects a
    spoofed content-type / non-image), then RE-ENCODES to WebP — which drops all
    EXIF/GPS and other metadata (we never pass ``exif=``). KYC media must be a
    raster photo, so SVG/PDF and anything Pillow can't decode are rejected.
    Returns ``(clean_bytes, "image/webp")``; raises HTTPException on bad input.

    ``formats`` narrows the allowlist for callers that want a shorter one; the
    default accepts anything Pillow decodes, because the re-encode is what makes
    the output safe rather than the input format.

    Size is checked TWICE and the second one matters: the byte cap says nothing
    about a decompression bomb, whose whole trick is to be small. The declared
    dimensions are read from the header and refused there, before any row is
    expanded — after decoding, the memory has already been taken.
    """
    from app.core.upload_guard import UploadRejected, check_raster_limits, looks_hostile

    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_file")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file_too_large")
    # Name the real reason for the formats we never accept. Without this, an
    # uploaded HTML page comes back as "unsupported_image_type", which is true
    # but tells neither the user nor the log what actually happened.
    hostile = looks_hostile(data)
    if hostile:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, hostile)
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "image_processing_unavailable") from None
    try:
        probe = Image.open(BytesIO(data))
        detected = (probe.format or "").upper()
        size = probe.size
        probe.verify()   # magic-byte sniff; raises on non-image/corrupt
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_image_type") from None
    if formats is not None and detected not in formats:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_image_type")
    try:
        check_raster_limits(*size)
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from None
    # verify() consumes the image — reopen, bake in orientation, then re-encode.
    img = ImageOps.exif_transpose(Image.open(BytesIO(data)))
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")
    img.thumbnail((MAX_DIM, MAX_DIM))
    out = BytesIO()
    img.save(out, format="WEBP", quality=WEBP_QUALITY, method=6)  # no exif= → metadata dropped
    return out.getvalue(), "image/webp"


async def upload_image(data: bytes, content_type: str | None) -> str:
    """Optimize, store on archive.org, and return the public URL.

    Raises HTTPException with a stable ``detail`` on any validation/transport
    error so the API layer can surface it unchanged.
    """
    access, secret = settings.archive_s3_access, settings.archive_s3_secret
    if not access or not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "media_upload_not_configured")

    # (#171) Magic-byte sniff + re-encode to WebP: rejects a spoofed content-type
    # and SVG/PDF (XSS vectors when served inline) and strips EXIF — replaces the
    # old client-MIME trust + format passthrough.
    data, ct = await asyncio.to_thread(sanitize_image, data, content_type)  # (#122) Pillow off event loop
    ext = "webp"

    ident = f"muddato-img-{pysecrets.token_hex(6)}"
    fname = f"image.{ext}"
    put_url = f"https://s3.us.archive.org/{ident}/{fname}"
    headers = {
        "authorization": f"LOW {access}:{secret}",
        "x-amz-auto-make-bucket": "1",
        "x-archive-meta-mediatype": "image",
        "x-archive-meta-collection": "opensource_media",
        "x-archive-meta-title": ident,
        "content-type": ct,
    }
    async def _put() -> httpx.Response:
        async with httpx.AsyncClient(timeout=90) as client:
            return await client.put(put_url, content=data, headers=headers)
    try:
        # (#149) archive.org uploads are flaky; retry with backoff. Idempotent —
        # the object key (ident) is fixed above, so each attempt re-PUTs the same blob.
        # (#255) Under a SUSTAINED archive.org outage the breaker trips OPEN and
        # fails fast, so uploads stop piling up retries that hold the request.
        from app.core.breaker import BreakerOpenError, archive_breaker
        r = await archive_breaker.call(lambda: retry_http(_put, label="archive_upload"))
    except BreakerOpenError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "upload_unavailable") from None
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "upload_transport_error") from None
    if r.status_code not in (200, 201):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"upload_failed_{r.status_code}")
    return f"https://archive.org/download/{ident}/{fname}"


def make_kyc_ref(*, value: str, tenant_id: str) -> str:
    """Opaque, tamper-proof, tenant-bound reference to a private KYC media value.

    ``value`` is a ``KycBlob`` id (new, private) or a legacy public URL (during
    the archive.org→private transition). Fernet-encrypts ``{t, v}`` (#107/#113) so
    the underlying value is never handed to the client, and the ref is usable only
    by the owning tenant (the proxy checks ``t`` before it streams the bytes).
    """
    return encrypt_secret(json.dumps({"t": str(tenant_id), "v": value}, separators=(",", ":")))


def read_kyc_ref(ref: str) -> tuple[str, str] | None:
    """Return ``(tenant_id, value)`` from a ref, or None if invalid/tampered.

    ``value`` is a ``KycBlob`` id (new) or a legacy public URL (transition). Reads
    both the new ``v`` key and the legacy ``u`` key for refs minted pre-#113."""
    raw = decrypt_secret(ref)
    if not raw or not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
        value = data.get("v") or data.get("u")
        if value is None:
            return None
        return str(data["t"]), str(value)
    except (ValueError, KeyError, TypeError):
        return None


async def store_kyc_image(
    db: AsyncSession, data: bytes, content_type: str | None, *, tenant_id: UUID,
) -> str:
    """Sanitize a KYC image (#114) and store it PRIVATELY (#113); return the
    ``KycBlob`` id to persist in the customer/guarantor photo list.

    Bytes go to the configured private object store (``kyc/<id>.webp``), else to
    Postgres (``KycBlob.data``) — never to public archive.org. The id is only ever
    exposed to clients wrapped in a tenant-bound kyc-media ref. Caller commits."""
    from uuid import uuid4

    from app.models import KycBlob
    from app.services.receipt_storage import get_receipt_store

    clean, ct = await asyncio.to_thread(sanitize_image, data, content_type)  # (#122) Pillow off event loop
    store = get_receipt_store()
    blob_id = uuid4()
    blob = KycBlob(id=blob_id, tenant_id=tenant_id, content_type=ct)
    if store is not None:
        key = f"kyc/{blob_id}.webp"
        await store.put(key, clean, ct)
        blob.object_key = key
    else:
        blob.data = clean
    db.add(blob)
    await db.flush()
    return str(blob_id)


def sanitize_receipt(data: bytes, content_type: str | None = None) -> tuple[bytes, str]:
    """Sanitize a receipt upload — the only gate a payment proof passes through.

    The declared ``content_type`` is NOT consulted for the decision. It is a
    claim written by whoever is uploading, and a receipt ends up in front of a
    reviewer who can approve money, so the routing is decided by the bytes:

    * a raster is decoded and RE-ENCODED to WebP, which drops EXIF/GPS and
      anything appended after the image data, because the output is built from
      the pixel array and nothing else;
    * a PDF bank slip cannot be re-encoded without a renderer we do not
      install, so it is structurally validated instead (real trailer, bounded
      pages, no active content) and passed through;
    * everything else is refused by name.

    The old version of this function checked five bytes — ``data[:5] ==
    b"%PDF-"`` — and passed the entire rest of the file through untouched. A
    document that began with that header and continued into HTML was stored and
    served back verbatim.
    """
    from app.core.upload_guard import UploadRejected, validate_pdf

    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_file")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file_too_large")

    if data.startswith(b"%PDF-"):
        try:
            validate_pdf(data)
        except UploadRejected as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason) from None
        return data, "application/pdf"

    return sanitize_image(data, content_type, formats=RECEIPT_IMAGE_FORMATS)


async def store_receipt(
    db: AsyncSession, content: bytes, content_type: str | None, *, tenant_id: UUID,
) -> str:
    """Sanitize (#114) + persist a receipt PRIVATELY; return its signed capability
    URL (``/api/receipts/<id>?s=<hmac>``). Shared by the customer cash-receipt and
    tenant top-up (#115) flows. Bytes go to a private object store, else Postgres —
    never public archive.org. Caller commits."""
    from uuid import uuid4

    from app.api.receipts import receipt_url
    from app.models import ReceiptBlob
    from app.services.receipt_storage import get_receipt_store, receipt_object_key

    clean, ct = await asyncio.to_thread(sanitize_receipt, content, content_type)  # (#122) Pillow off event loop
    store = get_receipt_store()
    blob_id = uuid4()
    blob = ReceiptBlob(id=blob_id, tenant_id=tenant_id, content_type=ct)
    if store is not None:
        key = receipt_object_key(blob_id, ct)
        await store.put(key, clean, ct)
        blob.object_key = key
    else:
        blob.data = clean
    db.add(blob)
    await db.flush()
    return receipt_url(str(blob_id))


_MEDIA_EXT = {
    "image/webp": "webp", "image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf",
}


async def store_tenant_media(
    db: AsyncSession,
    *,
    content: bytes,
    content_type: str | None,
    tenant_id: UUID | None,
    entity_type: str,
    entity_id: UUID | None = None,
    uploaded_by: UUID | None = None,
    filename: str | None = None,
    idempotency_key: str | None = None,
) -> str:
    """Sanitize + store a tenant file via the Telegram-group media system (#814),
    returning its signed media proxy URL (``/api/media/<id>?s=…``).

    Gated by config (see ``docs/TELEGRAM-MEDIA.md``):

    * ``media_fallback_mode=dual_write`` (default) → keep a durable copy in the
      private receipt store (source of truth for financial proofs) AND send to the
      tenant's Telegram topic. A failed Telegram send is recorded (``status=failed``)
      but the file is still served from the durable copy — no data loss.
    * ``telegram_only`` → Telegram reference only (cheapest); a failed send raises
      502 so the upload isn't silently lost.

    Callers reach this only when ``telegram_media_enabled`` is on; otherwise the
    legacy :func:`store_receipt` path is used. KYC images never use this path —
    they stay private via :func:`store_kyc_image`. Caller commits."""
    from uuid import uuid4

    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.api.media import media_url
    from app.models import MediaFile
    from app.services.receipt_storage import get_receipt_store
    from app.services.telegram_media import (
        TelegramMediaError,
        TelegramTopicMissing,
        ensure_tenant_topic,
        send_document_to_topic,
    )

    # (#817) Idempotency: a retry of the same logical upload (same tenant + the same
    # server-derived key) returns the existing row's proxy URL instead of sending a
    # duplicate document. A sequential retry short-circuits here before any sanitize
    # / durable write / send; a concurrent double-submit is caught by the unique
    # (tenant_id, idempotency_key) index when the row is inserted below (one row
    # wins; the loser's send is an orphan the reconcile path (#7) sweeps).
    async def _existing_url() -> str | None:
        existing_id = (await db.execute(
            select(MediaFile.id).where(
                MediaFile.tenant_id == tenant_id,
                MediaFile.idempotency_key == idempotency_key,
            )
        )).scalar_one_or_none()
        return media_url(str(existing_id)) if existing_id is not None else None

    if idempotency_key is not None:
        hit = await _existing_url()
        if hit is not None:
            return hit

    # (#114) Same sanitize as receipts: images re-encoded to WebP (EXIF/GPS
    # stripped + magic-byte sniffed), PDFs magic-validated, empty/oversize rejected.
    clean, ct = await asyncio.to_thread(sanitize_receipt, content, content_type)  # (#122) off the loop

    blob_id = uuid4()
    ext = _MEDIA_EXT.get(ct, "bin")
    name = filename or f"{entity_type}-{blob_id}.{ext}"
    keep_durable = settings.media_fallback_mode != "telegram_only"

    # Durable copy FIRST (source of truth) when dual-write is on, so a later
    # Telegram failure can never lose the file.
    object_key: str | None = None
    data_blob: bytes | None = None
    if keep_durable:
        store = get_receipt_store()
        if store is not None:
            object_key = f"media/{blob_id}.{ext}"
            await store.put(object_key, clean, ct)
        else:
            data_blob = clean

    # Send to the tenant's Telegram topic (platform-owner media → General topic 1).
    thread_id: int | None = None
    ref = None
    status_ = "stored"
    err: str | None = None
    try:
        thread_id = await ensure_tenant_topic(db, tenant_id)
        try:
            ref = await send_document_to_topic(clean, filename=name, thread_id=thread_id, caption=entity_type)
        except TelegramTopicMissing:
            # The tenant's topic was deleted on Telegram's side — recreate it once
            # and retry, so an admin removing a topic can't permanently wedge uploads.
            log.warning("media.topic_missing_recreating tenant=%s", tenant_id)
            thread_id = await ensure_tenant_topic(db, tenant_id, force_recreate=True)
            ref = await send_document_to_topic(clean, filename=name, thread_id=thread_id, caption=entity_type)
    except TelegramMediaError as e:
        if not keep_durable:
            # telegram_only + the send failed → the upload truly failed; surface it.
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "media_upload_failed") from None
        status_ = "failed"
        err = str(e)[:500]
        log.warning("media.telegram_send_failed entity=%s err=%s", entity_type, e)

    mf = MediaFile(
        id=blob_id,
        tenant_id=tenant_id,
        chat_id=(settings.telegram_media_chat_id if ref else None),
        message_thread_id=(thread_id if ref else None),
        message_id=(ref.message_id if ref else None),
        file_id=(ref.file_id if ref else None),
        file_unique_id=(ref.file_unique_id if ref else None),
        bot_owner="platform",
        original_filename=name,
        mime_type=ct,
        size_bytes=len(clean),
        entity_type=entity_type,
        entity_id=entity_id,
        uploaded_by=uploaded_by,
        status=status_,
        error=err,
        object_key=object_key,
        data=data_blob,
        idempotency_key=idempotency_key,
    )
    if idempotency_key is None:
        db.add(mf)
        await db.flush()
        return media_url(str(blob_id))

    # With a key, guard the insert in a savepoint: a concurrent double-submit hits
    # the unique (tenant_id, idempotency_key) index, so we reuse the winner's row
    # instead of failing the upload. (Our send already happened; that orphan message
    # is swept by the reconcile path, #7.)
    try:
        async with db.begin_nested():
            db.add(mf)
            await db.flush()
    except IntegrityError:
        hit = await _existing_url()
        if hit is not None:
            return hit
        raise
    return media_url(str(blob_id))


def payment_proof_idempotency_key(
    *, entity_type: str, discriminator: str, content: bytes,
) -> str:
    """A stable digest of one logical payment-proof upload (#817).

    Hashes ``entity_type`` + a caller-supplied ``discriminator`` (the request's
    immutable fields — e.g. installment+amount, or amount+method+card) + the raw
    bytes. A retry of the same request yields the same key (→ dedup); a different
    target, amount, or photo yields a different key (→ a new upload)."""
    import hashlib

    h = hashlib.sha256()
    h.update(f"{entity_type}|{discriminator}|".encode())
    h.update(content)
    return h.hexdigest()


async def store_payment_proof(
    db: AsyncSession,
    content: bytes,
    content_type: str | None,
    *,
    tenant_id: UUID,
    entity_type: str,
    uploaded_by: UUID | None = None,
    idempotency_discriminator: str | None = None,
) -> str:
    """Store a payment-proof upload — a customer cash receipt or a tenant top-up
    bank slip — and return its signed capability URL (#820).

    One gate for every payment-proof path so they can't diverge: when the
    Telegram-group media system is enabled (#814) the proof is routed there (sent to
    the tenant's topic + dual-write durable copy) and served by ``/api/media``;
    otherwise it goes to the legacy private receipt store (#96/#114), served by
    ``/api/receipts``. Bytes are sanitized + stored privately either way.

    ``idempotency_discriminator`` (the request's immutable fields) makes a retried
    upload idempotent on the Telegram path: it derives the key the media row dedups
    on (#817). The legacy receipt path doesn't send a Telegram message, so it needs
    no key."""
    if settings.telegram_media_enabled and settings.telegram_media_chat_id is not None:
        idem = (
            payment_proof_idempotency_key(
                entity_type=entity_type, discriminator=idempotency_discriminator, content=content,
            )
            if idempotency_discriminator is not None
            else None
        )
        return await store_tenant_media(
            db, content=content, content_type=content_type, tenant_id=tenant_id,
            entity_type=entity_type, uploaded_by=uploaded_by, idempotency_key=idem,
        )
    return await store_receipt(db, content, content_type, tenant_id=tenant_id)
