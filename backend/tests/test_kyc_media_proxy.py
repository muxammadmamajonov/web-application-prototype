"""#107 — KYC photos are served through an authenticated, tenant-bound proxy and
are never returned to the client as raw world-readable storage URLs.

DB-free guards: the ref round-trips, rejects tampering, and binds the tenant; the
serializer rewrites raw URLs into proxy URLs idempotently (leaving non-http
values alone and not exposing the underlying public URL); and the proxy endpoint
rejects an anonymous caller (401). Cross-tenant rejection (403) is exercised by
the CI integration tier.
"""
from __future__ import annotations


def test_kyc_ref_roundtrips_and_rejects_tampering() -> None:
    from app.services.media import make_kyc_ref, read_kyc_ref

    url = "https://archive.org/download/muddato-img-abc123/image.webp"
    ref = make_kyc_ref(value=url, tenant_id="tenant-123")
    assert read_kyc_ref(ref) == ("tenant-123", url)
    assert read_kyc_ref("not-a-real-ref") is None       # legacy/plaintext → rejected
    assert read_kyc_ref(ref + "tamper") is None          # tampered token → rejected
    # (#113) A private KycBlob id round-trips through the same ref.
    bref = make_kyc_ref(value="0c1d2e3f-aaaa-bbbb-cccc-000011112222", tenant_id="t9")
    assert read_kyc_ref(bref) == ("t9", "0c1d2e3f-aaaa-bbbb-cccc-000011112222")


def test_serializer_wraps_raw_kyc_urls_idempotently() -> None:
    from app.api.tenant import _proxy_kyc

    wrapped = _proxy_kyc(["https://archive.org/download/x/y.webp"], "tenant-123")
    assert len(wrapped) == 1
    assert wrapped[0].startswith("/api/tenant/kyc-media?ref=")
    assert "archive.org" not in wrapped[0]               # raw public URL not exposed
    # Idempotent: a proxied URL fed back in is not double-wrapped.
    assert _proxy_kyc(wrapped, "tenant-123") == wrapped
    assert _proxy_kyc(None, "tenant-123") == []
    # (#113) A private KycBlob id (non-http, non-proxied) is now ALSO wrapped.
    blob = _proxy_kyc(["0c1d2e3f-aaaa-bbbb-cccc-000011112222"], "tenant-123")
    assert blob[0].startswith("/api/tenant/kyc-media?ref=")


async def test_kyc_media_proxy_requires_auth(async_client) -> None:
    r = await async_client.get("/api/tenant/kyc-media?ref=anything")
    assert r.status_code == 401
