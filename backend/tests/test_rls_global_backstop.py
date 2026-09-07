"""Global-table RLS is least-privilege under an active tenant scope."""
from __future__ import annotations

import json

from sqlalchemy import text


async def _scoped(engine, tenant_id: str, sql: str, params: dict | None = None):
    async with engine.connect() as conn:
        tx = await conn.begin()
        try:
            await conn.execute(text("SET LOCAL ROLE muddato_app"))
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id},
            )
            return (await conn.execute(text(sql), params or {})).all()
        finally:
            await tx.rollback()


async def test_tenant_scope_cannot_read_platform_provider_secrets(migrated_db) -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    async with migrated_db.begin() as conn:
        await conn.execute(text(
            "INSERT INTO platform_settings (id,key,value,created_at,updated_at) VALUES "
            "(gen_random_uuid(),'billing_cards',CAST(:cards AS jsonb),now(),now()),"
            "(gen_random_uuid(),'billing_providers',CAST(:providers AS jsonb),now(),now())"
        ), {
            "cards": json.dumps({"items": []}),
            "providers": json.dumps({"payme": {"secret": "must-not-leak"}}),
        })

    keys = await _scoped(migrated_db, tenant_id, "SELECT key FROM platform_settings ORDER BY key")
    assert ("billing_cards",) in keys
    assert ("billing_providers",) not in keys


async def test_tenant_scope_only_sees_users_related_to_that_tenant(migrated_db) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole, TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as session:
        ta = Tenant(slug="global-rls-a", name="A", status=TenantStatus.active)
        tb = Tenant(slug="global-rls-b", name="B", status=TenantStatus.active)
        ua = User(telegram_id=910001)
        ub = User(telegram_id=910002)
        session.add_all([ta, tb, ua, ub])
        await session.flush()
        session.add_all([
            TenantUser(tenant_id=ta.id, user_id=ua.id, role=TenantRole.owner),
            TenantUser(tenant_id=tb.id, user_id=ub.id, role=TenantRole.owner),
        ])
        await session.commit()

    visible = await _scoped(migrated_db, str(ta.id), "SELECT telegram_id FROM users ORDER BY telegram_id")
    assert visible == [(910001,)]


async def test_sequence_counter_is_hidden_but_security_definer_increment_works(migrated_db) -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    rows = await _scoped(
        migrated_db,
        tenant_id,
        "SELECT public.next_invoice_sequence(2099), public.next_invoice_sequence(2099)",
    )
    assert rows == [(1, 2)]
    assert await _scoped(migrated_db, tenant_id, "SELECT name FROM sequence_counters") == []
