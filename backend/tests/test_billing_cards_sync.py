"""Platform receiving-card → tenant top-up SYNC (integration, RLS-on).

Reproduces the reported bug: an admin adds a receiving card in Platform →
Settings → Cards (a ``platform_settings.billing_cards`` row written as the
privileged role), but a tenant's balance top-up modal shows "Hali qabul qiluvchi
karta sozlanmagan" — i.e. the tenant's RLS-scoped DB session (the restricted
``muddato_app`` role with ``app.tenant_id`` pinned) reads zero cards.

``platform_settings`` is a GLOBAL table (not in TENANT_TABLES, so no RLS) and the
restricted role holds SELECT on it, so a tenant scope MUST still read it. If a
future migration ever enables RLS on it or drops the grant, this fails — which is
exactly the cross-context "sync" break to guard against. Skips without a reachable
test Postgres (the integration tier runs in CI).
"""
from __future__ import annotations

import json

from sqlalchemy import text

_TENANT_GUC = "11111111-1111-1111-1111-111111111111"
_CARD = {"id": "card-1", "number": "8600000000001234", "bank": "Kapital", "scheme": "uzcard"}


async def _save_billing_cards_as_admin(engine, cards: list[dict]) -> None:
    """Mimic the platform admin's PUT /platform/settings/billing_cards: written on
    the privileged connection with NO tenant GUC (platform endpoints don't scope)."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO platform_settings (id, key, value, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'billing_cards', CAST(:v AS jsonb), now(), now())"
            ),
            {"v": json.dumps({"items": cards})},
        )


async def _read_as_tenant(engine, sql: str) -> object:
    """Run ``sql`` exactly like a tenant request: restricted role + app.tenant_id."""
    async with engine.connect() as conn:
        tr = await conn.begin()
        try:
            await conn.execute(text("SET LOCAL ROLE muddato_app"))
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": _TENANT_GUC}
            )
            return (await conn.execute(text(sql))).scalar_one()
        finally:
            await tr.rollback()


async def test_billing_cards_saved_by_admin_are_visible_to_tenant_scope(migrated_db) -> None:
    await _save_billing_cards_as_admin(migrated_db, [_CARD])

    # The tenant-scoped (RLS) session must still see the global billing_cards row.
    count = await _read_as_tenant(
        migrated_db, "SELECT count(*) FROM platform_settings WHERE key = 'billing_cards'"
    )
    assert count == 1, "tenant RLS scope cannot read the platform billing_cards setting"

    # …and the card payload round-trips so get_billing_cards() returns it.
    pan = await _read_as_tenant(
        migrated_db,
        "SELECT value->'items'->0->>'number' FROM platform_settings WHERE key = 'billing_cards'",
    )
    assert pan == _CARD["number"], "billing card payload is not readable under tenant scope"


async def test_get_billing_cards_service_returns_admin_saved_cards(migrated_db) -> None:
    """End-to-end through the actual service the top-up endpoint calls, on a
    tenant-scoped session — the layer that feeds the modal's card list."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services import subscription_checkout as sc

    await _save_billing_cards_as_admin(migrated_db, [_CARD])

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        await s.execute(text("SET LOCAL ROLE muddato_app"))
        await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": _TENANT_GUC})
        cards = await sc.get_billing_cards(s)

    assert len(cards) == 1, "get_billing_cards returned no cards under tenant scope (the bug)"
    assert cards[0]["number"] == _CARD["number"]
