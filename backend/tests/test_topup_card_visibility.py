"""A card added by the platform must reach the tenant's top-up screen.

Reported symptom: the platform owner adds a receiving card, and tenant owners
still do not get the "top up balance" action. The tenant UI shows that action
only when `GET /tenant/billing/topup-info` returns a non-empty `cards` list, so
this file walks the whole path in one place:

    platform writes PlatformSetting['billing_cards']
        → get_billing_cards() reads it
            → /tenant/billing/topup-info returns it

Both shapes the setting has ever been written in are covered. The writer sends
``{"items": [...]}``; an older/hand-edited row may hold a bare list, and
`get_billing_cards` claims to accept either — so the claim is tested rather
than trusted.
"""
from __future__ import annotations

import pytest


async def _write_billing_cards(session_factory, value) -> None:
    """Write the platform setting exactly as the settings API stores it."""
    from sqlalchemy import select

    from app.models import PlatformSetting

    agen = session_factory()
    db = await agen.__anext__()
    try:
        row = (await db.execute(
            select(PlatformSetting).where(PlatformSetting.key == "billing_cards")
        )).scalar_one_or_none()
        if row is None:
            db.add(PlatformSetting(key="billing_cards", value=value))
        else:
            row.value = value
        await db.commit()
    finally:
        await agen.aclose()


CARD = {"id": "c1", "number": "8600000000001234", "holder": "ALISHER V", "bank": "NBU", "scheme": "uzcard"}


@pytest.fixture
def factory(isolation_ctx):
    from app.db import get_db
    from app.main import app
    return app.dependency_overrides[get_db]


# ── the service layer ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_wrapped_shape_the_settings_api_writes_is_read_back(isolation_ctx, factory):
    """`PUT /platform/settings/{key}` wraps the body, so this is the real shape."""
    from app.services.subscription_checkout import get_billing_cards

    await _write_billing_cards(factory, {"items": [CARD]})
    agen = factory()
    db = await agen.__anext__()
    try:
        assert await get_billing_cards(db) == [CARD]
    finally:
        await agen.aclose()


@pytest.mark.asyncio
async def test_a_bare_list_is_read_back_too(isolation_ctx, factory):
    from app.services.subscription_checkout import get_billing_cards

    await _write_billing_cards(factory, [CARD])
    agen = factory()
    db = await agen.__anext__()
    try:
        assert await get_billing_cards(db) == [CARD]
    finally:
        await agen.aclose()


@pytest.mark.asyncio
async def test_no_setting_means_no_cards_not_a_crash(isolation_ctx, factory):
    from app.services.subscription_checkout import get_billing_cards

    agen = factory()
    db = await agen.__anext__()
    try:
        assert await get_billing_cards(db) == []
    finally:
        await agen.aclose()


# ── the endpoint the tenant screen actually calls ────────────────────────────

@pytest.mark.asyncio
async def test_the_tenant_endpoint_surfaces_the_card(isolation_ctx, factory):
    """The one that matters: what the tenant's browser receives."""
    client, ctx = isolation_ctx
    await _write_billing_cards(factory, {"items": [CARD]})

    r = await client.get("/api/tenant/billing/topup-info", headers={**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cards"], f"tenant sees no cards — top-up stays hidden. body={body}"
    assert body["cards"][0]["number"] == CARD["number"]


@pytest.mark.asyncio
async def test_without_a_card_the_endpoint_reports_none(isolation_ctx, factory):
    """The other half: the disabled state must be driven by real emptiness."""
    client, ctx = isolation_ctx
    r = await client.get("/api/tenant/billing/topup-info", headers={**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]})
    assert r.status_code == 200, r.text
    assert r.json()["cards"] == []
