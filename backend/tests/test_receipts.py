"""C3 — receipt blob storage + signed capability URL."""
from __future__ import annotations

from app.core.security import sign_receipt, verify_receipt_sig


def test_receipt_sig_roundtrips() -> None:
    bid = "11111111-1111-1111-1111-111111111111"
    sig = sign_receipt(bid)
    assert verify_receipt_sig(bid, sig) is True


def test_receipt_sig_rejects_tampering() -> None:
    bid = "11111111-1111-1111-1111-111111111111"
    sig = sign_receipt(bid)
    assert verify_receipt_sig(bid, sig + "x") is False
    assert verify_receipt_sig(bid, None) is False
    assert verify_receipt_sig(bid, "") is False
    # Signature is bound to the specific blob id.
    assert verify_receipt_sig("22222222-2222-2222-2222-222222222222", sig) is False


def test_receipt_url_shape() -> None:
    from app.api.receipts import receipt_url

    bid = "33333333-3333-3333-3333-333333333333"
    url = receipt_url(bid)
    assert url.startswith(f"/api/receipts/{bid}?s=")
    # The embedded signature must validate.
    sig = url.split("s=", 1)[1]
    assert verify_receipt_sig(bid, sig) is True


def test_receipt_blob_model_registered() -> None:
    from app.models import ReceiptBlob

    assert ReceiptBlob.__tablename__ == "receipt_blobs"
    cols = set(ReceiptBlob.__table__.columns.keys())
    assert {"id", "tenant_id", "content_type", "data"} <= cols
