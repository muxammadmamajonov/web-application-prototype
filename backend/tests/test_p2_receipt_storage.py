"""#96 — receipt image bytes → object storage (keep them out of Postgres).

Receipt photos were stored as ``receipt_blobs.data`` (LargeBinary) in the primary
DB, which bloats WAL + backups at scale. This moves the *bytes* to object storage
(Supabase Storage, a private bucket) while the DB keeps only a reference
(``receipt_blobs.object_key``) + the existing HMAC-signed capability URL — so the
access model is unchanged (the app still proxies the bytes behind ``sign_receipt``;
the storage bucket is never exposed).

Pluggable by design: ``get_receipt_store()`` returns the configured object store,
or ``None`` meaning "keep using Postgres" (the zero-config default, so dev / test /
an un-provisioned deploy behave exactly as before). A future S3/R2 backend is a
drop-in implementation of the same ``ReceiptStore`` protocol.

DB-free: the object-key scheme, the store factory, the Supabase HTTP request
construction (via httpx MockTransport — no network), config derivation, the
migration content, and that the upload/serve paths branch on ``object_key``.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import httpx
import pytest

_MIG = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "r74_receipt_object_storage.py"
)


# ── object-key scheme ────────────────────────────────────────────────────────

def test_receipt_object_key_has_id_and_extension() -> None:
    from app.services.receipt_storage import receipt_object_key

    k = receipt_object_key("abc-123", "image/webp")
    assert k == "receipts/abc-123.webp"
    # unknown content type falls back to a generic extension, never crashes
    assert receipt_object_key("x", "application/octet-stream").startswith("receipts/x.")


# ── store factory ────────────────────────────────────────────────────────────

def test_factory_returns_none_for_postgres_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings
    from app.services.receipt_storage import get_receipt_store

    monkeypatch.setattr(settings, "receipt_storage_backend", "postgres")
    assert get_receipt_store() is None  # None ⇒ keep bytes in Postgres


def test_factory_returns_supabase_store_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings
    from app.services.receipt_storage import SupabaseReceiptStore, get_receipt_store

    monkeypatch.setattr(settings, "receipt_storage_backend", "supabase")
    monkeypatch.setattr(settings, "supabase_service_key", "svc-key")
    monkeypatch.setattr(settings, "supabase_project_ref", "myref")
    store = get_receipt_store()
    assert isinstance(store, SupabaseReceiptStore)
    assert store.backend == "supabase"


def test_factory_raises_if_supabase_selected_but_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings
    from app.services.receipt_storage import ReceiptStorageError, get_receipt_store

    monkeypatch.setattr(settings, "receipt_storage_backend", "supabase")
    monkeypatch.setattr(settings, "supabase_service_key", None)
    with pytest.raises(ReceiptStorageError):
        get_receipt_store()


# ── config derivation ────────────────────────────────────────────────────────

def test_supabase_base_url_derives_from_project_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "supabase_url", None)
    monkeypatch.setattr(settings, "supabase_project_ref", "uxsbkikd")
    assert settings.supabase_base_url == "https://uxsbkikd.supabase.co"
    # an explicit URL overrides the derived one
    monkeypatch.setattr(settings, "supabase_url", "https://custom.example")
    assert settings.supabase_base_url == "https://custom.example"


# ── Supabase store HTTP construction (mocked, no network) ─────────────────────

def _store(captured: list[httpx.Request], *, status: int = 200, body: bytes = b"", ct: str = "image/webp"):
    from app.services.receipt_storage import SupabaseReceiptStore

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(status, content=body, headers={"content-type": ct})

    return SupabaseReceiptStore(
        base_url="https://ref.supabase.co",
        service_key="svc-key",
        bucket="receipts",
        transport=httpx.MockTransport(handler),
    )


async def test_supabase_put_uploads_to_private_bucket_with_bearer() -> None:
    cap: list[httpx.Request] = []
    await _store(cap, status=200).put("receipts/a.webp", b"IMGBYTES", "image/webp")
    req = cap[-1]
    assert req.method == "PUT"
    assert str(req.url) == "https://ref.supabase.co/storage/v1/object/receipts/receipts/a.webp"
    assert req.headers["authorization"] == "Bearer svc-key"
    assert req.headers["content-type"] == "image/webp"
    assert req.content == b"IMGBYTES"


async def test_supabase_get_returns_bytes_and_content_type() -> None:
    cap: list[httpx.Request] = []
    got = await _store(cap, status=200, body=b"IMG", ct="image/png").get("receipts/a.png")
    assert got == (b"IMG", "image/png")
    assert cap[-1].method == "GET"
    assert cap[-1].headers["authorization"] == "Bearer svc-key"


async def test_supabase_get_returns_none_on_404() -> None:
    cap: list[httpx.Request] = []
    assert await _store(cap, status=404).get("receipts/missing.webp") is None


async def test_supabase_put_raises_on_error_status() -> None:
    from app.services.receipt_storage import ReceiptStorageError
    cap: list[httpx.Request] = []
    with pytest.raises(ReceiptStorageError):
        await _store(cap, status=500).put("receipts/a.webp", b"x", "image/webp")


# ── migration r74 ────────────────────────────────────────────────────────────

def test_migration_adds_object_key_and_makes_data_nullable() -> None:
    src = _MIG.read_text(encoding="utf-8")
    assert 'revision = "r74receiptobj"' in src
    assert 'down_revision = "r73rlsisolation"' in src
    assert "object_key" in src
    # legacy `data` must become nullable so object-stored rows don't carry bytes
    assert "data" in src and "nullable" in src.lower()


# ── upload + serve wiring ────────────────────────────────────────────────────

def test_store_receipt_uses_object_store_when_configured() -> None:
    import app.api.me as me
    from app.services import media

    # The object-store branching lives in the shared helper (#114/#115); the
    # customer cash-receipt path delegates to it.
    src = inspect.getsource(media.store_receipt)
    assert "get_receipt_store" in src
    assert "object_key" in src
    assert "store_receipt" in inspect.getsource(me._store_receipt)


def test_serve_receipt_streams_from_object_store_when_keyed() -> None:
    import app.api.receipts as r

    src = inspect.getsource(r.serve_receipt)
    # branch on object_key: keyed ⇒ fetch from the store; else legacy blob.data
    assert "object_key" in src
    assert "get_receipt_store" in src
