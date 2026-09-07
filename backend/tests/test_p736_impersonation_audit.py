"""#736 — every impersonated MUTATING request leaves an audit trace attributable
to the entering super-admin, even via a handler that doesn't call audit.record
(or records a non-allowlisted action that would be silently dropped).

resolve_tenant is the single chokepoint where impersonation is detected, so it
records ``tenant.impersonated_mutation`` (with ``_impersonated_by``) for any
mutating method under impersonation, committed durably. Reads don't trigger it.
"""
from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import AuditLog, Tenant, User
from app.models.enums import PlatformRole


def _req(method: str, path: str):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


async def _seed_admin_and_tenant(s) -> tuple[User, Tenant]:
    # A platform super-admin who is NOT a member of the tenant → impersonation path.
    admin = User(telegram_id=973601, platform_role=PlatformRole.super_admin, is_active=True)
    tenant = Tenant(slug="t-imp-736", name="Imp 736")
    s.add_all([admin, tenant])
    await s.flush()
    await s.commit()
    return admin, tenant


async def _impersonated_mutation_rows(Session) -> list[AuditLog]:
    async with Session() as s:  # fresh (non-RLS-scoped) session reads all rows
        return list((await s.execute(
            select(AuditLog).where(AuditLog.action == "tenant.impersonated_mutation")
        )).scalars().all())


async def test_impersonated_mutation_is_audited(migrated_db) -> None:
    from app.api.tenant import resolve_tenant
    from app.services import audit

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    try:
        async with Session() as s:
            admin, tenant = await _seed_admin_and_tenant(s)
            _, membership = await resolve_tenant(
                request=_req("POST", "/api/tenant/customers"),
                user=admin, db=s, x_tenant_id=str(tenant.id),
            )
            assert getattr(membership, "impersonated", False) is True

        rows = await _impersonated_mutation_rows(Session)
        assert len(rows) == 1, "an impersonated POST must leave exactly one trace"
        row = rows[0]
        assert row.actor_user_id == admin.id
        assert row.tenant_id == tenant.id
        # the request is attributable to the super-admin, with method + path captured
        assert row.after_json["_impersonated_by"] == str(admin.id)
        assert row.after_json["method"] == "POST"
        assert row.after_json["path"] == "/api/tenant/customers"
    finally:
        audit.set_impersonator(None)  # don't leak the contextvar to other tests


async def test_impersonated_read_is_not_audited_as_mutation(migrated_db) -> None:
    from app.api.tenant import resolve_tenant
    from app.services import audit

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    try:
        async with Session() as s:
            admin, tenant = await _seed_admin_and_tenant(s)
            await resolve_tenant(
                request=_req("GET", "/api/tenant/overview"),
                user=admin, db=s, x_tenant_id=str(tenant.id),
            )
        assert await _impersonated_mutation_rows(Session) == [], \
            "a read under impersonation must not be recorded as a mutation"
    finally:
        audit.set_impersonator(None)
