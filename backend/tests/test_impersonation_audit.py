"""(#127) An audit row written while a super-admin impersonates a tenant carries
impersonated_by_user_id — so every impersonated MUTATION is queryable, not just the
super_admin_enter row."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import AuditLog, User
from app.services import audit


async def test_impersonated_mutation_is_flagged(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    try:
        async with Session() as s:
            admin = User(telegram_id=970001)
            s.add(admin)
            await s.flush()

            audit.set_impersonator(None)                      # not impersonating
            await audit.record(s, action="payment.approve", actor_user_id=admin.id,
                               entity_type="payment", entity_id="p1")

            audit.set_impersonator(admin.id)                  # impersonating
            await audit.record(s, action="installment.edit", actor_user_id=admin.id,
                               entity_type="installment", entity_id="i1")
            await s.commit()

            rows = {r.action: r for r in (await s.execute(select(AuditLog))).scalars().all()}
            assert (rows["payment.approve"].after_json or {}).get("_impersonated_by") is None
            assert rows["installment.edit"].after_json["_impersonated_by"] == str(admin.id)
    finally:
        audit.set_impersonator(None)                          # don't leak into other tests
