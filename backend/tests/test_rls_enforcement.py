"""#95 — row-level security enforced at the database layer (integration).

These run against a real Postgres with the REAL schema (``alembic upgrade head``,
so the ``muddato_app`` role + policies from r73 exist) and prove the *database*
blocks cross-tenant access even if application code were to forget its WHERE —
the defense-in-depth backstop behind the app-level isolation (test_tenant_isolation).

Mirrors the production cutover verification as a permanent regression guard:
a wrong/dropped policy or a missing grant fails this suite. Skips without a
reachable test Postgres (the CI `integration` job provides one).
"""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

_FAKE = "00000000-0000-0000-0000-0000000000ff"


async def _seed_two_tenants(engine) -> tuple[str, str]:
    from app.models import Customer, Tenant

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        ta, tb = Tenant(slug="rls-a", name="RLS A"), Tenant(slug="rls-b", name="RLS B")
        s.add_all([ta, tb])
        await s.flush()
        s.add_all([
            Customer(tenant_id=ta.id, full_name="A One", phone="+998900000011"),
            Customer(tenant_id=tb.id, full_name="B One", phone="+998900000012"),
        ])
        await s.commit()
        return str(ta.id), str(tb.id)


async def _scoped(engine, guc: str | None, sql: str, params: dict | None = None):
    """Run ``sql`` as the restricted role with app.tenant_id = ``guc`` (or unset),
    rolling back so the probe never mutates the DB."""
    async with engine.connect() as conn:
        tr = await conn.begin()
        try:
            await conn.execute(text("SET LOCAL ROLE muddato_app"))
            if guc is not None:
                await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": guc})
            return (await conn.execute(text(sql), params or {})).scalar_one()
        finally:
            await tr.rollback()


async def test_rls_backstop_filters_customers_by_tenant_guc(migrated_db) -> None:
    a, b = await _seed_two_tenants(migrated_db)
    # (Batch 5) No GUC → fail-CLOSED, sees nothing. This assertion used to read
    # `== 2`: an unscoped transaction under the restricted role saw every
    # tenant's customers, which is what made "a route forgot resolve_tenant"
    # indistinguishable from "this is the platform". Platform and scheduler work
    # now says so with an explicit authority role instead.
    assert await _scoped(migrated_db, None, "SELECT count(*) FROM customers") == 0
    # Blank and malformed are the same answer as unset — never an error, never a row.
    assert await _scoped(migrated_db, "", "SELECT count(*) FROM customers") == 0
    assert await _scoped(migrated_db, "not-a-uuid", "SELECT count(*) FROM customers") == 0
    # Scoped to a tenant → sees only that tenant's row.
    assert await _scoped(migrated_db, a, "SELECT count(*) FROM customers") == 1
    assert await _scoped(migrated_db, b, "SELECT count(*) FROM customers") == 1
    # A foreign/unknown tenant → 0 rows (RLS *excludes* non-matching, the real proof).
    assert await _scoped(migrated_db, _FAKE, "SELECT count(*) FROM customers") == 0
    # The one row visible under GUC=a is genuinely A's.
    leaked = await _scoped(
        migrated_db, a, "SELECT count(*) FROM customers WHERE tenant_id <> :a", {"a": a}
    )
    assert leaked == 0


async def test_rls_with_check_blocks_cross_tenant_write(migrated_db) -> None:
    a, b = await _seed_two_tenants(migrated_db)
    blocked = False
    async with migrated_db.connect() as conn:
        tr = await conn.begin()
        try:
            await conn.execute(text("SET LOCAL ROLE muddato_app"))
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": a})
            # Try to move A's customer into tenant B → WITH CHECK must reject it.
            await conn.execute(
                text("UPDATE customers SET tenant_id = :b WHERE tenant_id = :a"),
                {"a": a, "b": b},
            )
        except Exception as exc:
            blocked = any(s in str(exc).lower() for s in ("policy", "row-level security", "violates"))
        finally:
            await tr.rollback()
    assert blocked, "WITH CHECK should block moving a row to another tenant"


async def test_platform_owned_null_tenant_rows_are_invisible_to_a_tenant(migrated_db) -> None:
    """(Batch 5) A ``tenant_id IS NULL`` row belongs to the PLATFORM, not to
    everyone.

    This assertion is inverted from what it was. r73 gave 27 policies a
    ``tenant_id IS NULL`` branch on the theory that NULL meant "shared", and on
    ``catalog_models`` it does — that is the global product catalogue every shop
    reads. On ``receipt_blobs``, ``invitations``, ``audit_log``, ``notifications``,
    ``media_files`` and ``webhook_dlq`` it means the opposite: a platform-owned
    row that no tenant should see. Those are now platform/worker-only, and the
    genuine shared catalogue keeps its read branch — which the second half of
    this test pins, so the fix cannot be over-applied.
    """
    from app.models import CatalogBrand, CatalogModel

    a, _ = await _seed_two_tenants(migrated_db)
    async with migrated_db.begin() as conn:
        await conn.execute(text(
            "INSERT INTO receipt_blobs (id, tenant_id, content_type, created_at, updated_at) "
            "VALUES (gen_random_uuid(), NULL, 'image/webp', now(), now())"
        ))
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        brand = CatalogBrand(slug="rls-brand", name="RLS Brand")
        s.add(brand)
        await s.flush()
        s.add(CatalogModel(
            tenant_id=None, brand_id=brand.id,
            slug="rls-global-model", name="Global Model",
        ))
        await s.commit()

    hidden = await _scoped(
        migrated_db, a, "SELECT count(*) FROM receipt_blobs WHERE tenant_id IS NULL"
    )
    assert hidden == 0, "a platform-owned NULL-tenant row must not be visible to a tenant"
    # (MUDD-113) r97 now seeds ~125 real global models into every migrated_db,
    # so a raw total-row count is no longer a stable "just this fixture" probe.
    # Look up the fixture's own row by its unique slug instead.
    shared = await _scoped(
        migrated_db, a,
        "SELECT count(*) FROM catalog_models WHERE tenant_id IS NULL AND slug = 'rls-global-model'",
    )
    assert shared == 1, "the shared product catalogue must stay readable"


async def test_apply_rls_scope_filters_via_the_real_app_path(migrated_db, monkeypatch) -> None:
    """#124 — exercise the ACTUAL application path: db._apply_rls_scope (what
    resolve_tenant calls) with rls_enabled=True. A forgotten-WHERE query must
    still be DB-filtered to the scoped tenant. Catches a regression that re-disables
    RLS or breaks the SET LOCAL ROLE / set_config wiring."""
    from app.config import settings
    from app.db import _apply_rls_scope
    from app.models import Customer

    a, _b = await _seed_two_tenants(migrated_db)          # seed with RLS still OFF
    monkeypatch.setattr(settings, "rls_enabled", True)    # now turn it on, like prod

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        await _apply_rls_scope(s, a)                      # the real app entrypoint
        # A genuinely forgotten WHERE: no tenant_id filter anywhere.
        rows = (await s.execute(select(Customer))).scalars().all()
        tenant_ids = [str(r.tenant_id) for r in rows]    # read while still bound
        await s.rollback()
    assert tenant_ids == [a], "RLS must filter the forgotten-WHERE query to the scoped tenant only"


async def test_every_tenant_scoped_table_has_rls(migrated_db) -> None:
    """#124/#95 — EVERY table with a tenant_id column must have RLS enabled, so a
    newly added tenant table can't silently ship without the DB-level isolation
    backstop (r73's TENANT_TABLES list is hand-maintained — this catches a miss)."""
    async with migrated_db.connect() as conn:
        rows = (await conn.execute(text(
            """
            SELECT c.relname, c.relrowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
              AND EXISTS (
                SELECT 1 FROM information_schema.columns col
                WHERE col.table_schema = 'public' AND col.table_name = c.relname
                  AND col.column_name = 'tenant_id'
              )
            """
        ))).all()
    missing = sorted(name for name, rls in rows if not rls)
    assert missing == [], f"tenant-scoped tables missing RLS (no isolation backstop): {missing}"
