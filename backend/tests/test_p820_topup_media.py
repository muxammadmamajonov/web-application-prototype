"""#820 — both payment-proof uploads (customer cash receipt + tenant top-up bank
slip) go through ONE shared gate, ``media.store_payment_proof``, so they can't
diverge. The gate routes to the Telegram-group media system when enabled, else to
the legacy private receipt store (backward-compatible).
"""
from __future__ import annotations

import inspect
from uuid import uuid4


def test_both_payment_proof_paths_use_shared_gate() -> None:
    import app.api.me as me
    import app.api.tenant as tenant
    from app.services import media

    # customer cash-receipt path delegates to the gate
    assert "store_payment_proof" in inspect.getsource(me._store_receipt)
    # tenant top-up path delegates to the gate and tags its own entity_type
    topup = inspect.getsource(tenant.tenant_create_topup)
    assert "store_payment_proof" in topup
    assert "tenant_topup" in topup
    # the gate itself is what routes by the feature flag
    gate = inspect.getsource(media.store_payment_proof)
    assert "telegram_media_enabled" in gate
    assert "store_tenant_media" in gate
    assert "store_receipt" in gate


async def test_store_payment_proof_routes_by_flag(monkeypatch) -> None:
    from app.config import settings
    from app.services import media

    calls: list[tuple[str, str | None]] = []

    async def _fake_media(db, *, content, content_type, tenant_id, entity_type,
                          uploaded_by=None, idempotency_key=None):
        calls.append(("media", entity_type))
        return "/api/media/abc?s=1"

    async def _fake_receipt(db, content, content_type, *, tenant_id):
        calls.append(("receipt", None))
        return "/api/receipts/abc?s=1"

    monkeypatch.setattr(media, "store_tenant_media", _fake_media)
    monkeypatch.setattr(media, "store_receipt", _fake_receipt)
    tid = uuid4()

    # enabled + chat configured → Telegram media path, preserving the entity_type
    monkeypatch.setattr(settings, "telegram_media_enabled", True)
    monkeypatch.setattr(settings, "telegram_media_chat_id", -1001234567891)
    url = await media.store_payment_proof(None, b"x", "image/png", tenant_id=tid, entity_type="tenant_topup")
    assert url.startswith("/api/media/")
    assert calls[-1] == ("media", "tenant_topup")

    # disabled (the default) → legacy private receipt store — backward compatible
    monkeypatch.setattr(settings, "telegram_media_enabled", False)
    url2 = await media.store_payment_proof(None, b"x", "image/png", tenant_id=tid, entity_type="tenant_topup")
    assert url2.startswith("/api/receipts/")
    assert calls[-1] == ("receipt", None)

    # enabled but no chat id configured → defensive fallback to the legacy store
    monkeypatch.setattr(settings, "telegram_media_enabled", True)
    monkeypatch.setattr(settings, "telegram_media_chat_id", None)
    await media.store_payment_proof(None, b"x", "image/png", tenant_id=tid, entity_type="payment_receipt")
    assert calls[-1] == ("receipt", None)
