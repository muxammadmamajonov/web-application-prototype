"""#113/#114 — KYC media is private + sanitized (EXIF/GPS stripped, sniffed).

KYC passport/ID/guarantor photos must never be world-readable (they used to be
uploaded to public archive.org). They now go to a private store (KycBlob, Postgres
by default or a private bucket) and are served only through the tenant-authenticated
/tenant/kyc-media proxy. Uploads are re-encoded to strip EXIF/GPS and sniffed so a
spoofed content-type can't smuggle a non-image (#114).
"""
from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


def _jpeg_with_exif() -> bytes:
    from PIL import Image

    img = Image.new("RGB", (24, 24), (200, 30, 30))
    exif = img.getexif()
    exif[0x0112] = 3                       # Orientation
    exif[0x010E] = "taken at a private home address"  # ImageDescription (stand-in for GPS/PII metadata)
    buf = BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def test_sanitize_strips_all_metadata_and_returns_webp() -> None:
    from PIL import Image

    from app.services.media import sanitize_image

    src = _jpeg_with_exif()
    assert len(Image.open(BytesIO(src)).getexif()) > 0  # the input HAS metadata

    clean, ct = sanitize_image(src, "image/jpeg")
    assert ct == "image/webp"
    out = Image.open(BytesIO(clean))
    assert out.format == "WEBP"
    assert len(out.getexif()) == 0  # EXIF/GPS gone


def test_sanitize_rejects_a_non_image() -> None:
    from app.services.media import sanitize_image

    with pytest.raises(HTTPException) as e:
        sanitize_image(b"<svg onload=alert(1)>not really an image", "image/png")
    assert e.value.status_code == 400


def test_sanitize_rejects_empty() -> None:
    from app.services.media import sanitize_image

    with pytest.raises(HTTPException):
        sanitize_image(b"", "image/png")


# ── #113 private KYC storage + tenant-gated proxy (real PG) ───────────────────

def _owner_membership():
    from app.models import TenantUser
    from app.models.enums import TenantRole

    return TenantUser(role=TenantRole.owner)


async def test_kyc_image_stored_privately_and_served_to_owner(migrated_db) -> None:
    from app.api.tenant import kyc_media
    from app.models import KycBlob, Tenant
    from app.services.media import make_kyc_ref, store_kyc_image

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="kyc-a", name="KYC A")
        s.add(t)
        await s.flush()
        tid = t.id
        key = await store_kyc_image(s, _jpeg_with_exif(), "image/jpeg", tenant_id=tid)
        await s.commit()

    # Stored PRIVATELY: a KycBlob with bytes in Postgres, no public URL anywhere.
    async with Session() as s:
        blob = await s.get(KycBlob, key)
        assert blob is not None
        assert blob.object_key is None and blob.data           # bytes kept private in DB
        assert blob.content_type == "image/webp"               # sanitized

    # Served only via the tenant-bound proxy.
    ref = make_kyc_ref(value=key, tenant_id=str(tid))
    async with Session() as s:
        t = await s.get(Tenant, str(tid))
        resp = await kyc_media(ref=ref, ctx=(t, _owner_membership()), db=s)
    assert resp.media_type == "image/webp"
    assert resp.body


async def test_kyc_media_cross_tenant_forbidden(migrated_db) -> None:
    from app.api.tenant import kyc_media
    from app.models import Tenant
    from app.services.media import make_kyc_ref, store_kyc_image

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        a, b = Tenant(slug="kyc-a", name="A"), Tenant(slug="kyc-b", name="B")
        s.add_all([a, b])
        await s.flush()
        key = await store_kyc_image(s, _jpeg_with_exif(), "image/jpeg", tenant_id=a.id)
        a_id, b_id = str(a.id), str(b.id)
        await s.commit()

    ref = make_kyc_ref(value=key, tenant_id=a_id)  # bound to tenant A
    async with Session() as s:
        b = await s.get(Tenant, b_id)
        with pytest.raises(HTTPException) as e:
            await kyc_media(ref=ref, ctx=(b, _owner_membership()), db=s)  # tenant B may not fetch A's KYC
    assert e.value.status_code == 403


async def test_kyc_media_rejects_tampered_ref(migrated_db) -> None:
    from app.api.tenant import kyc_media
    from app.models import Tenant

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="kyc-c", name="C")
        s.add(t)
        await s.flush()
        tid = str(t.id)
        await s.commit()
    async with Session() as s:
        t = await s.get(Tenant, tid)
        with pytest.raises(HTTPException) as e:
            await kyc_media(ref="not-a-valid-ref", ctx=(t, _owner_membership()), db=s)
    assert e.value.status_code == 400


async def test_kyc_media_sales_role_forbidden(migrated_db) -> None:
    """(MUDD-77) The role gate on /kyc-media must actually reject non-owner/manager
    staff, not just owners passing through — a passport/ID scan is the same PII
    class as the masked fields, so `sales` may never fetch it."""
    from app.api.tenant import kyc_media
    from app.models import Tenant, TenantUser
    from app.models.enums import TenantRole
    from app.services.media import make_kyc_ref, store_kyc_image

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="kyc-d", name="D")
        s.add(t)
        await s.flush()
        tid = t.id
        key = await store_kyc_image(s, _jpeg_with_exif(), "image/jpeg", tenant_id=tid)
        await s.commit()

    ref = make_kyc_ref(value=key, tenant_id=str(tid))
    async with Session() as s:
        t = await s.get(Tenant, str(tid))
        sales = TenantUser(role=TenantRole.sales)
        with pytest.raises(HTTPException) as e:
            await kyc_media(ref=ref, ctx=(t, sales), db=s)
    assert e.value.status_code == 403


async def test_kyc_media_none_membership_forbidden_not_crash(migrated_db) -> None:
    """(MUDD-77) A caller resolved with no tenant membership must get a clean 403
    from the role gate, never an AttributeError → 500. `_require_owner_or_manager`
    is fail-closed on `membership=None`."""
    from app.api.tenant import kyc_media
    from app.models import Tenant
    from app.services.media import make_kyc_ref, store_kyc_image

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="kyc-e", name="E")
        s.add(t)
        await s.flush()
        tid = t.id
        key = await store_kyc_image(s, _jpeg_with_exif(), "image/jpeg", tenant_id=tid)
        await s.commit()

    ref = make_kyc_ref(value=key, tenant_id=str(tid))
    async with Session() as s:
        t = await s.get(Tenant, str(tid))
        with pytest.raises(HTTPException) as e:
            await kyc_media(ref=ref, ctx=(t, None), db=s)
    assert e.value.status_code == 403


# ── #114/#115 receipts: EXIF-stripped + stored privately ─────────────────────

def test_sanitize_receipt_strips_exif_from_image() -> None:
    from PIL import Image

    from app.services.media import sanitize_receipt

    clean, ct = sanitize_receipt(_jpeg_with_exif(), "image/jpeg")
    assert ct == "image/webp"
    assert len(Image.open(BytesIO(clean)).getexif()) == 0



def _real_pdf(pages: int = 1) -> bytes:
    """A structurally complete one-page PDF.

    The old fixtures here were header fragments — `b"%PDF-1.4 minimal"` — and
    they passed because the guard checked five bytes and passed the rest
    through. That is the defect, so the fixture has to be a real file now.
    """
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [3 0 R] /Count {pages} >>".encode(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def test_sanitize_receipt_allows_pdf() -> None:
    from app.services.media import sanitize_receipt

    pdf = _real_pdf()
    clean, ct = sanitize_receipt(pdf, "application/pdf")
    assert ct == "application/pdf" and clean == pdf


def test_sanitize_receipt_refuses_a_header_without_a_pdf_behind_it() -> None:
    """The fixture this file used to accept.

    `%PDF-` followed by anything at all was stored and served back verbatim,
    so a header glued in front of an HTML document reached a signed-in
    reviewer's browser.
    """
    from app.services.media import sanitize_receipt

    for fragment in (
        b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>",       # no xref, no EOF
        b"%PDF-1.4\n<html><script>alert(1)</script></html>",
    ):
        with pytest.raises(HTTPException) as ei:
            sanitize_receipt(fragment, "application/pdf")
        assert ei.value.status_code == 400


def test_sanitize_receipt_rejects_junk() -> None:
    from app.services.media import sanitize_receipt

    with pytest.raises(HTTPException):
        sanitize_receipt(b"not an image or a pdf", "image/png")


async def test_store_receipt_is_private_not_public(migrated_db) -> None:
    from app.models import ReceiptBlob, Tenant
    from app.services.media import store_receipt

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="rcpt-t", name="R")
        s.add(t)
        await s.flush()
        tid = t.id
        url = await store_receipt(s, _jpeg_with_exif(), "image/jpeg", tenant_id=tid)
        await s.commit()
    assert url.startswith("/api/receipts/")   # signed capability URL
    assert "archive.org" not in url           # never public
    async with Session() as s:
        blob = (await s.execute(
            select(ReceiptBlob).where(ReceiptBlob.tenant_id == tid)
        )).scalar_one()
        assert blob.object_key is None and blob.data       # private bytes in Postgres
        assert blob.content_type == "image/webp"           # EXIF-stripped on the way in
