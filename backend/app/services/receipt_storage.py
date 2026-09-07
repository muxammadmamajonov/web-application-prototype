"""Pluggable storage for customer-uploaded receipt images (#96).

Receipt *bytes* used to live in ``receipt_blobs.data`` (Postgres LargeBinary),
which bloats the primary DB / WAL / backups at scale. This module lets the bytes
move to a private object-storage bucket while Postgres keeps only a reference
(``receipt_blobs.object_key``) + the existing HMAC capability URL — so access
stays gated by ``sign_receipt`` and the bucket itself is never exposed (the app
proxies the bytes via the receipts endpoint).

Design
------
``get_receipt_store()`` returns the configured store, or ``None`` meaning "keep
the bytes in Postgres" (the zero-config default — dev / test / an un-provisioned
deploy behave exactly as before). The ``ReceiptStore`` protocol is intentionally
tiny (put / get / delete of opaque bytes by key); a future S3/R2 backend is a
drop-in. The Supabase backend talks to Supabase Storage's REST API with a plain
``Authorization: Bearer <service_key>`` (no SigV4), matching the app's existing
raw-httpx S3 style and keeping the dependency surface minimal.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from app.config import settings

# content-type → file extension for the object key (cosmetic; the served
# content-type comes from the stored object's own header on the way back out).
_EXT = {
    "image/webp": "webp", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/png": "png", "image/gif": "gif", "image/svg+xml": "svg",
    "application/pdf": "pdf",
}

_HTTP_TIMEOUT = 30.0


def receipt_object_key(blob_id: object, content_type: str | None) -> str:
    """Deterministic object key for a receipt blob: ``receipts/<id>.<ext>``."""
    ext = _EXT.get((content_type or "").lower(), "bin")
    return f"receipts/{blob_id}.{ext}"


@runtime_checkable
class ReceiptStore(Protocol):
    backend: str

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> tuple[bytes, str] | None: ...
    async def delete(self, key: str) -> None: ...


class ReceiptStorageError(RuntimeError):
    """Object-storage transport/Config failure — surfaced so callers fail loudly
    rather than silently dropping a receipt or serving a blank image."""


class SupabaseReceiptStore:
    """Supabase Storage backend (private bucket, server-side service-role auth)."""

    backend = "supabase"

    def __init__(
        self,
        *,
        base_url: str,
        service_key: str,
        bucket: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._key = service_key
        self._bucket = bucket
        self._transport = transport  # injectable for tests (MockTransport)

    def _object_url(self, key: str) -> str:
        return f"{self._base}/storage/v1/object/{self._bucket}/{key}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            transport=self._transport,
            headers={"authorization": f"Bearer {self._key}"},
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            async with self._client() as client:
                r = await client.put(
                    self._object_url(key),
                    content=data,
                    headers={"content-type": content_type, "x-upsert": "true"},
                )
        except httpx.HTTPError as exc:
            raise ReceiptStorageError(f"receipt_put_transport_error: {exc}") from None
        if r.status_code not in (200, 201):
            raise ReceiptStorageError(f"receipt_put_failed_{r.status_code}")

    async def get(self, key: str) -> tuple[bytes, str] | None:
        try:
            async with self._client() as client:
                r = await client.get(self._object_url(key))
        except httpx.HTTPError as exc:
            raise ReceiptStorageError(f"receipt_get_transport_error: {exc}") from None
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ReceiptStorageError(f"receipt_get_failed_{r.status_code}")
        ct = r.headers.get("content-type", "application/octet-stream")
        return r.content, ct

    async def delete(self, key: str) -> None:
        try:
            async with self._client() as client:
                r = await client.delete(self._object_url(key))
        except httpx.HTTPError as exc:
            raise ReceiptStorageError(f"receipt_delete_transport_error: {exc}") from None
        if r.status_code not in (200, 204, 404):
            raise ReceiptStorageError(f"receipt_delete_failed_{r.status_code}")

    async def ensure_bucket(self) -> None:
        """Create the private bucket if it doesn't exist (idempotent). Used by the
        backfill / activation step, not on the hot request path."""
        try:
            async with self._client() as client:
                r = await client.post(
                    f"{self._base}/storage/v1/bucket",
                    json={"id": self._bucket, "name": self._bucket, "public": False},
                )
        except httpx.HTTPError as exc:
            raise ReceiptStorageError(f"bucket_create_transport_error: {exc}") from None
        # 200 created; 400/409 "already exists" is fine.
        if r.status_code not in (200, 201, 400, 409):
            raise ReceiptStorageError(f"bucket_create_failed_{r.status_code}")


def get_receipt_store() -> ReceiptStore | None:
    """The configured object store, or ``None`` to keep bytes in Postgres."""
    backend = (settings.receipt_storage_backend or "postgres").lower()
    if backend in ("", "postgres", "db"):
        return None
    if backend == "supabase":
        base = settings.supabase_base_url
        if not base or not settings.supabase_service_key:
            raise ReceiptStorageError(
                "receipt_storage_backend=supabase but SUPABASE_URL/PROJECT_REF or "
                "SUPABASE_SERVICE_KEY is unset"
            )
        return SupabaseReceiptStore(
            base_url=base,
            service_key=settings.supabase_service_key,
            bucket=settings.receipt_bucket,
        )
    raise ReceiptStorageError(f"unknown receipt_storage_backend: {backend!r}")
