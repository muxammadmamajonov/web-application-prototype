"""Mutation proofs: each defence, deliberately broken, must be caught (Part 7).

A passing security suite proves nothing on its own — it might be asserting
things that are true for reasons unrelated to the defence. So every test here
re-opens one hole, runs the assertion that is supposed to notice, and requires
it to FAIL. Then it puts the defence back and requires the same assertion to
pass again, so a mutation that merely broke the fixture is not mistaken for a
mutation that was caught.

Each proof names the specific assertion it exercises, because "something went
red" is not evidence: what matters is that the *security* claim went red.
"""
from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import text

from app.core.rls_manifest import AUTHORITY_ROLES, CAPABILITY_FUNCTIONS
from tests import _dbcreds

from ._rls_world import count, denied, probe

#: The DB-backed proofs share a module-scoped world (and therefore a
#: module-scoped loop); the two static proofs are plain functions.
_module_loop = pytest.mark.asyncio(loop_scope="module")

_NO_SCOPE = "NULLIF(current_setting('app.tenant_id', true), '') IS NULL"
_SCOPE = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


@contextlib.asynccontextmanager
async def mutate(world, apply_sql: list[str], restore_sql: list[str]):
    """Apply a mutation, hand back control, and always put it back."""
    async with world.engine.begin() as conn:
        for sql in apply_sql:
            await conn.execute(text(sql))
    try:
        yield
    finally:
        async with world.engine.begin() as conn:
            for sql in restore_sql:
                await conn.execute(text(sql))


def _restore_strict(table: str) -> list[str]:
    """Put a strict tenant policy back exactly as rf0failclosed created it."""
    return [
        f"DROP POLICY IF EXISTS rls_{table}_app ON {table}",
        f"DROP POLICY IF EXISTS mutant ON {table}",
        f"CREATE POLICY rls_{table}_app ON {table} FOR ALL TO muddato_app "
        "USING (tenant_id = public.app_current_tenant()) "
        "WITH CHECK (tenant_id = public.app_current_tenant())",
    ]


async def _assert_isolated(world, table: str) -> None:
    """THE assertion under proof: an unscoped session reads none of ``table``,
    and a scoped one reads none of the other tenant's rows."""
    assert await count(world, "muddato_app", table) == 0, (
        f"unscoped read returned rows from {table}"
    )
    rows = await probe(
        world, "muddato_app", f"SELECT count(*) FROM {table} WHERE tenant_id = :b",
        tenant=world.tenant_a, params={"b": world.tenant_b},
    )
    assert rows[0][0] == 0, f"tenant A read tenant B's {table}"


# ── 1. unset context grants access ───────────────────────────────────────────

@_module_loop
async def test_reintroducing_the_no_scope_branch_is_caught(rls_world) -> None:
    """r73's shape: "no tenant named ⇒ every row". The original finding."""
    await _assert_isolated(rls_world, "customers")
    async with mutate(
        rls_world,
        ["DROP POLICY rls_customers_app ON customers",
         "CREATE POLICY mutant ON customers FOR ALL TO muddato_app "
         f"USING ({_NO_SCOPE} OR tenant_id = {_SCOPE}) "
         f"WITH CHECK ({_NO_SCOPE} OR tenant_id = {_SCOPE})"],
        _restore_strict("customers"),
    ):
        with pytest.raises(AssertionError, match="unscoped read"):
            await _assert_isolated(rls_world, "customers")
    await _assert_isolated(rls_world, "customers")


# ── 2. tenant_id IS NULL grants access ───────────────────────────────────────

@_module_loop
async def test_reintroducing_the_null_tenant_branch_is_caught(rls_world) -> None:
    async def _null_row_is_hidden() -> None:
        rows = await probe(
            rls_world, "muddato_app",
            "SELECT count(*) FROM receipt_blobs WHERE tenant_id IS NULL",
            tenant=rls_world.tenant_a,
        )
        assert rows[0][0] == 0, "a platform-owned NULL-tenant row was visible"

    async with rls_world.engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO receipt_blobs (id, tenant_id, content_type, created_at, "
            "updated_at) VALUES (gen_random_uuid(), NULL, 'image/webp', now(), now())"
        ))
    try:
        await _null_row_is_hidden()
        async with mutate(
            rls_world,
            ["DROP POLICY rls_receipt_blobs_app ON receipt_blobs",
             "CREATE POLICY mutant ON receipt_blobs FOR ALL TO muddato_app "
             "USING (tenant_id IS NULL OR tenant_id = public.app_current_tenant()) "
             "WITH CHECK (tenant_id = public.app_current_tenant())"],
            _restore_strict("receipt_blobs"),
        ):
            with pytest.raises(AssertionError, match="NULL-tenant row"):
                await _null_row_is_hidden()
        await _null_row_is_hidden()
    finally:
        async with rls_world.engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM receipt_blobs WHERE tenant_id IS NULL"))


# ── 3. WITH CHECK removed ────────────────────────────────────────────────────

@_module_loop
async def test_removing_with_check_lets_a_row_be_planted_and_is_caught(rls_world) -> None:
    """USING filters what you can SEE; WITH CHECK is what stops you WRITING a
    row into another tenant. Drop it and reads still look perfectly correct.

    Proved on INSERT rather than on "move this row to tenant B", because
    PostgreSQL refuses that move even with ``WITH CHECK (true)``: on UPDATE it
    also requires the NEW row to satisfy the role's visibility, so a row cannot
    be updated out of the scope that is allowed to see it. That is a real second
    layer, and it is exactly why the mutation has to be an INSERT — otherwise
    this proof would pass on the strength of a defence it is not testing.
    """
    async def _cannot_plant_a_row() -> None:
        assert await denied(
            rls_world, "muddato_app",
            "INSERT INTO customers "
            "(id, tenant_id, full_name, phone, passport_photo_urls, status, metadata) "
            "VALUES (gen_random_uuid(), :b, 'Intruder', '+998900000777', '{}', "
            "'active', '{}'::jsonb)",
            tenant=rls_world.tenant_a,
            params={"b": rls_world.tenant_b},
        ), "a customer row was planted in another tenant"

    await _cannot_plant_a_row()
    async with mutate(
        rls_world,
        ["DROP POLICY rls_customers_app ON customers",
         "CREATE POLICY mutant ON customers FOR ALL TO muddato_app "
         "USING (tenant_id = public.app_current_tenant()) "
         "WITH CHECK (true)"],
        _restore_strict("customers"),
    ):
        with pytest.raises(AssertionError, match="planted in another tenant"):
            await _cannot_plant_a_row()
    await _cannot_plant_a_row()


# ── 4. a runtime role owns a protected table ─────────────────────────────────

@_module_loop
async def test_giving_a_runtime_role_ownership_is_caught(rls_world) -> None:
    """Ownership bypasses row security outright — no policy is consulted."""
    async def _no_runtime_role_owns_anything() -> None:
        rows = await probe(
            rls_world, "muddato_worker",
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind='r' "
            "AND pg_get_userbyid(c.relowner) IN "
            "('muddato_app','muddato_customer','muddato_identity','muddato_worker',"
            " 'muddato_platform','muddato_runtime')",
        )
        assert rows == [], f"a runtime role owns {[r[0] for r in rows]}"

    await _no_runtime_role_owns_anything()
    owner = None
    async with rls_world.engine.begin() as conn:
        owner = (await conn.execute(text(
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname='customers'"
        ))).scalar_one()
    async with mutate(
        rls_world,
        # CREATE on the schema is a precondition of owning a table in it, so the
        # mutation has to grant it too; both are taken back on the way out.
        ["GRANT CREATE ON SCHEMA public TO muddato_app",
         "ALTER TABLE customers OWNER TO muddato_app"],
        [f"ALTER TABLE customers OWNER TO {owner}",
         "REVOKE CREATE ON SCHEMA public FROM muddato_app",
         *_restore_strict("customers")],
    ):
        with pytest.raises(AssertionError, match="owns"):
            await _no_runtime_role_owns_anything()
        # And the consequence, not just the catalogue fact: as the owner, an
        # unscoped session reads every tenant's customers again.
        leaked = await count(rls_world, "muddato_app", "customers")
        assert leaked == 2, "ownership should have bypassed the policy"
    await _no_runtime_role_owns_anything()
    await _assert_isolated(rls_world, "customers")


# ── 5. a runtime role gains BYPASSRLS ────────────────────────────────────────

@_module_loop
async def test_granting_bypassrls_is_caught(rls_world) -> None:
    """Two properties, and the FIRST one changed in the Batch 5 correction.

    This test used to encode a tolerance: "the connection role on a managed
    platform DOES carry BYPASSRLS, so the preflight must record it as a risk and
    let the deploy through". That tolerance was the finding. It made every
    connection opened outside SQLAlchemy's session machinery — a health check, a
    library's own pool, a future CLI — an unmonitored bypass, and it was
    unnecessary: the fix is to stop connecting as that role, not to accept it.

    So a BYPASSRLS runtime login is now a FAILURE, and the second property (that
    assuming an authority role strips the privilege) stays exactly as it was —
    defence in depth, not the defence.
    """
    from app.cli.rls_preflight import Report, _judge

    def _report() -> Report:
        r = Report(
            current_user="muddato_api", expected_runtime_role="muddato_api",
            expected_head="x", alembic_head="x",
            rolcanlogin=True, rolinherit=False,
            raw_probed_tables=48, raw_refused_tables=48,
            migration_checked=True, migration_is_separate=True,
            migration_owns_tables=48, migration_user="postgres",
            same_database=True,
        )
        r.runtime_db = {
            "name": "postgres", "oid": "5", "cluster": "7",
            "cluster_source": "system_identifier",
        }
        r.migration_db = dict(r.runtime_db)
        r.login_memberships = sorted(AUTHORITY_ROLES)
        r.roles = {
            name: {"rolsuper": False, "rolbypassrls": False,
                   "rolcanlogin": False, "rolinherit": True}
            for name in ("muddato_runtime", *AUTHORITY_ROLES)
        }
        r.effective_after_downgrade = dict.fromkeys(AUTHORITY_ROLES, (False, False))
        r.capability_functions_ok = len(CAPABILITY_FUNCTIONS)
        return r

    clean = _report()
    _judge(clean)
    assert clean.failures == [], clean.failures

    # MUTATION 1 — the login itself bypasses row security. Previously a
    # recorded risk; now a deploy blocker.
    bypassing = _report()
    bypassing.rolbypassrls = True
    _judge(bypassing)
    assert any("BYPASSRLS" in f for f in bypassing.failures), (
        "a BYPASSRLS runtime connection must block the deploy — it makes every "
        "non-Session code path an unmonitored bypass"
    )

    # MUTATION 2 — the downgrade stops stripping it.
    broken = _report()
    broken.effective_after_downgrade["muddato_app"] = (False, True)
    _judge(broken)
    assert any("does not drop BYPASSRLS" in f for f in broken.failures), (
        "the preflight must refuse an authority role that can bypass row security"
    )

    # MUTATION 3 — the login becomes INHERIT, so a raw connection silently holds
    # every authority's grants without ever calling SET ROLE.
    inheriting = _report()
    inheriting.rolinherit = True
    _judge(inheriting)
    assert any("NOINHERIT" in f for f in inheriting.failures)

    # And the same on the real database: assuming the role really does strip it.
    rows = await probe(
        rls_world, "muddato_app",
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user",
    )
    assert rows and rows[0] == (False, False), (
        f"SET LOCAL ROLE muddato_app left privileges in place: {rows}"
    )


# ── 6. a pooled connection keeps the previous tenant ─────────────────────────

@_module_loop
async def test_not_clearing_the_gucs_between_transactions_is_caught(rls_world) -> None:
    """The mutation is in the application, not the schema: stop rewriting the
    GUCs at transaction start and a checked-in connection carries a scope into
    the next request."""
    import app.db as db

    original = db._scope_statements

    def leaky() -> list[str]:
        role = db._db_authority.get().value
        out = [f"SET LOCAL ROLE {role}"]
        tid = db._canonical_uuid(db._request_tenant_id.get())
        if tid:                       # <- only writes when set; never clears
            out.append(f"SELECT set_config('app.tenant_id', '{tid}', true)")
        return out

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_dbcreds.render(rls_world.engine.url), pool_size=1, max_overflow=0)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        db._scope_statements = leaky  # type: ignore[assignment]
        from app.config import settings

        was = settings.rls_enabled
        settings.rls_enabled = True
        try:
            db.reset_db_scope()
            db._request_tenant_id.set(rls_world.tenant_a)
            async with Session() as s:
                await s.execute(text("SELECT 1"))
                # Leave the GUC behind at session level so the next checkout of
                # this very connection inherits it.
                await s.execute(text(
                    "SELECT set_config('app.tenant_id', :t, false)"),
                    {"t": rls_world.tenant_a},
                )
                await s.commit()
            db.reset_db_scope()
            async with Session() as s:
                leaked = (await s.execute(text(
                    "SELECT coalesce(current_setting('app.tenant_id', true), '')"
                ))).scalar_one()
            assert leaked == rls_world.tenant_a, (
                "the mutation did not actually leak; the proof would be vacuous"
            )
        finally:
            settings.rls_enabled = was
    finally:
        db._scope_statements = original  # type: ignore[assignment]
        db.reset_db_scope()

    # And with the real implementation, the same sequence does NOT leak.
    from app.config import settings

    was = settings.rls_enabled
    settings.rls_enabled = True
    try:
        db._request_tenant_id.set(rls_world.tenant_a)
        async with Session() as s:
            await s.execute(text("SELECT 1"))
            await s.commit()
        db.reset_db_scope()
        async with Session() as s:
            leaked = (await s.execute(text(
                "SELECT coalesce(current_setting('app.tenant_id', true), '')"
            ))).scalar_one()
        assert leaked == "", "a pooled connection carried a tenant scope forward"
    finally:
        settings.rls_enabled = was
        db.reset_db_scope()
        await engine.dispose()


# ── 7. a customer endpoint with global reach ─────────────────────────────────

@_module_loop
async def test_running_the_customer_surface_with_global_reach_is_caught(
    rls_world,
) -> None:
    """The mutation: grant ``/me`` the worker authority instead of the customer
    one. The assertion that must notice is "a customer sees only their shops"."""
    async def _customer_sees_one_shop(role: str) -> None:
        rows = await probe(
            rls_world, role, "SELECT id FROM customers", user=rls_world.customer_user,
        )
        assert [str(r[0]) for r in rows] == [rls_world.customer_a], (
            "the customer surface returned somebody else's customers"
        )

    await _customer_sees_one_shop("muddato_customer")
    with pytest.raises(AssertionError, match="somebody else"):
        await _customer_sees_one_shop("muddato_worker")

    # …and the route contract test is the static half of the same proof.
    from tests.test_rls_route_contract import (
        test_the_customer_surface_never_claims_more_than_customer_authority as guard,
    )

    guard()


# ── 8. a worker operation through the request role ───────────────────────────

@_module_loop
async def test_running_a_worker_scan_as_the_request_role_is_caught(rls_world) -> None:
    """A reminder sweep has to see every shop. Run it under the ordinary request
    authority and it silently reminds nobody — the failure mode that makes a
    fail-closed design dangerous if the worker is not wired explicitly."""
    async def _sweep_sees_every_shop(role: str) -> None:
        due = await count(rls_world, role, "installments")
        assert due == 2, f"the sweep saw {due} contracts, not every shop's"

    await _sweep_sees_every_shop("muddato_worker")
    with pytest.raises(AssertionError, match="not every shop"):
        await _sweep_sees_every_shop("muddato_app")


# ── 9–12. per-table isolation removed ────────────────────────────────────────

@pytest.mark.parametrize("table", (
    "kyc_blobs", "receipt_blobs", "invitations", "payments",
))
@_module_loop
async def test_removing_isolation_from_a_sensitive_table_is_caught(
    rls_world, table,
) -> None:
    """One proof each for the four tables the brief names: KYC, receipts,
    invitations, payments."""
    await _assert_isolated(rls_world, table)
    async with mutate(
        rls_world,
        [f"DROP POLICY rls_{table}_app ON {table}",
         f"CREATE POLICY mutant ON {table} FOR ALL TO muddato_app "
         "USING (true) WITH CHECK (true)"],
        _restore_strict(table),
    ):
        with pytest.raises(AssertionError):
            await _assert_isolated(rls_world, table)
    await _assert_isolated(rls_world, table)


# ── 13. the user self/link restriction removed ───────────────────────────────

@_module_loop
async def test_removing_the_user_directory_restriction_is_caught(rls_world) -> None:
    """An unscoped request must see exactly one user: itself."""
    async def _only_self() -> None:
        rows = await probe(
            rls_world, "muddato_app", "SELECT id FROM users", user=rls_world.owner_a,
        )
        assert [str(r[0]) for r in rows] == [rls_world.owner_a], (
            "the whole user directory was readable"
        )

    await _only_self()
    async with mutate(
        rls_world,
        ["DROP POLICY rls_users_app_select ON users",
         "CREATE POLICY mutant ON users FOR SELECT TO muddato_app USING (true)"],
        ["DROP POLICY IF EXISTS mutant ON users",
         "CREATE POLICY rls_users_app_select ON users FOR SELECT TO muddato_app "
         "USING (id = public.app_current_user() "
         "OR EXISTS (SELECT 1 FROM public.tenant_users tu "
         "WHERE tu.user_id = users.id AND tu.tenant_id = public.app_current_tenant()) "
         "OR EXISTS (SELECT 1 FROM public.customers c "
         "WHERE c.telegram_id = users.telegram_id "
         "AND c.tenant_id = public.app_current_tenant()))"],
    ):
        with pytest.raises(AssertionError, match="user directory"):
            await _only_self()
    await _only_self()


# ── 14. an unclassified tenant table ─────────────────────────────────────────

@_module_loop
async def test_adding_an_unclassified_tenant_table_is_caught(rls_world) -> None:
    """A new table must fail CI until a human classifies it. This is what stops
    the r94/r95/r99 pattern — a table nobody reasoned about — from recurring."""
    from tests.test_rls_matrix import test_every_public_table_is_classified as guard

    await guard(rls_world)
    async with mutate(
        rls_world,
        ["CREATE TABLE rls_mutant_table (id uuid PRIMARY KEY, tenant_id uuid)"],
        ["DROP TABLE IF EXISTS rls_mutant_table"],
    ):
        with pytest.raises(AssertionError, match="unclassified table"):
            await guard(rls_world)
    await guard(rls_world)


# ── 15. a tenant route with no tenant authorization ──────────────────────────

def test_a_tenant_route_without_resolve_tenant_is_caught() -> None:
    """Add a route under ``/api/tenant/`` that never resolves a tenant, and the
    static contract must notice. (The database notices too — it returns nothing —
    which ``test_rls_matrix`` proves; this is the fast, legible half.)"""
    from fastapi import APIRouter

    from app.main import app
    from tests.test_rls_route_contract import test_every_tenant_route_resolves_a_tenant

    test_every_tenant_route_resolves_a_tenant()

    rogue = APIRouter()

    @rogue.get("/api/tenant/rls-mutant-route")
    async def rls_mutant_route() -> dict:      # pragma: no cover — never called
        return {}

    app.include_router(rogue)
    try:
        with pytest.raises(AssertionError, match="no resolve_tenant"):
            test_every_tenant_route_resolves_a_tenant()
    finally:
        app.router.routes = [
            r for r in app.router.routes
            if getattr(r, "name", None) != "rls_mutant_route"
        ]
    test_every_tenant_route_resolves_a_tenant()
