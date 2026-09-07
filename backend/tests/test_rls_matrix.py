"""The row-level-security matrix, driven from the reviewed manifest.

Every protected table is exercised mechanically, per authority role, against
real Postgres with genuinely restricted roles — no mocks, no model-only schema,
no `rls_enabled` shortcut. The manifest (``app.core.rls_manifest``) decides what
gets tested, so a table nobody has classified cannot quietly pass: the first
test in this file fails on any unclassified table in schema ``public``.

Read alongside ``tests/test_rls_mutation_proofs.py``, which re-opens each of
these holes one at a time and checks that the assertion below actually notices.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.rls_manifest import (
    AUTHORITY_ROLES,
    CLASSIFICATION,
    ROLE_APP,
    ROLE_CUSTOMER,
    ROLE_IDENTITY,
    ROLE_PLATFORM,
    ROLE_RUNTIME,
    ROLE_WORKER,
    Category,
    tables_in,
)

from ._rls_world import (
    CLONE_INTO_OTHER_TENANT,
    count,
    denied,
    new_uuid,
    probe,
    refused_by_policy,
)

#: The world (and its asyncpg connections) live for the whole module, so
#: every test in it must run on the module-scoped loop that opened them.
pytestmark = pytest.mark.asyncio(loop_scope="module")

#: Tables whose rows are tenant property. The core of the matrix.
TENANT_TABLES = tables_in(
    Category.TENANT_STRICT, Category.TENANT_SELF, Category.TENANT_GLOBAL_READ,
)

#: The four tables the batch brief calls out by name, plus the two Batch 4 ones.
SENSITIVE = (
    "customers", "payments", "installments", "invitations",
    "kyc_blobs", "receipt_blobs", "document_deliveries",
    "payment_review_notices",
)


# ── the manifest is complete ─────────────────────────────────────────────────

async def test_every_public_table_is_classified(rls_world) -> None:
    """A new table must be classified before it can ship.

    This is the guard against the failure mode that produced r94, r95 and r99:
    each of those was a table that existed, carried data, and had been reasoned
    about by nobody. The manifest cannot auto-approve — an unlisted table fails
    here and the rest of this file never even generates a case for it.
    """
    rows = await probe(
        rls_world, ROLE_WORKER,
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'",
    )
    live = {r[0] for r in rows}
    assert not (live - set(CLASSIFICATION)), (
        "unclassified table(s): add them to app/core/rls_manifest.CLASSIFICATION "
        f"and give them a policy: {sorted(live - set(CLASSIFICATION))}"
    )
    assert not (set(CLASSIFICATION) - live), (
        f"manifest names table(s) that do not exist: {sorted(set(CLASSIFICATION) - live)}"
    )


# ── role posture ─────────────────────────────────────────────────────────────

async def test_authority_roles_are_not_superusers_and_cannot_bypass(rls_world) -> None:
    rows = await probe(
        rls_world, ROLE_WORKER,
        "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin, rolinherit "
        "FROM pg_roles WHERE rolname = ANY(:n)",
        params={"n": list((*AUTHORITY_ROLES, ROLE_RUNTIME))},
    )
    seen = {r[0]: r for r in rows}
    for role in (*AUTHORITY_ROLES, ROLE_RUNTIME):
        assert role in seen, f"{role} does not exist"
        _n, is_super, bypass, login, inherit = seen[role]
        assert not is_super, f"{role} is a SUPERUSER"
        assert not bypass, f"{role} has BYPASSRLS"
        assert not login, f"{role} can LOGIN — it must only ever be assumed"
        assert inherit, f"{role} is NOINHERIT and cannot reach its own grants"


async def test_no_authority_role_owns_a_protected_table(rls_world) -> None:
    """Ownership is the bypass that FORCE ROW LEVEL SECURITY exists to close.

    We deliberately do not FORCE (the schema owner has to be able to run
    backfills), so the property is guaranteed the other way: no role the runtime
    can assume owns anything.
    """
    rows = await probe(
        rls_world, ROLE_WORKER,
        "SELECT c.relname, pg_get_userbyid(c.relowner) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
        "AND pg_get_userbyid(c.relowner) = ANY(:n)",
        params={"n": list((*AUTHORITY_ROLES, ROLE_RUNTIME))},
    )
    assert rows == [], f"runtime role owns table(s): {rows}"


async def test_rls_is_enabled_on_every_classified_table(rls_world) -> None:
    """FORCE is off by design and RLS is on everywhere it can be.

    ``alembic_version`` is the single exception and is protected by REVOKE
    instead — enabling row security on it would mean taking ACCESS EXCLUSIVE on
    the table Alembic holds open while stamping the revision.
    """
    rows = await probe(
        rls_world, ROLE_WORKER,
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'",
    )
    from app.core.rls_manifest import FORCE_RLS_BY_CATEGORY

    for name, enabled, forced in rows:
        category = CLASSIFICATION[name]
        if category is Category.MIGRATION_ONLY:
            continue
        assert enabled, f"row security is OFF on {name}"
        # Per CATEGORY, not one blanket assertion: if a category's FORCE decision
        # is ever revisited, this reads the manifest rather than contradicting it.
        assert forced == FORCE_RLS_BY_CATEGORY[category], (
            f"{name} ({category}) has FORCE RLS = {forced}, manifest says "
            f"{FORCE_RLS_BY_CATEGORY[category]}"
        )


# ── tenant A vs tenant B, per table ──────────────────────────────────────────

@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_tenant_sees_only_its_own_rows(rls_world, table) -> None:
    """Whatever tenant A can see of ``table``, none of it belongs to tenant B."""
    scope_col = "id" if table == "tenants" else "tenant_id"
    if CLASSIFICATION[table] is Category.TENANT_GLOBAL_READ:
        # The shared catalogue is legitimately visible; the tenant-owned rows
        # are the ones that must not cross.
        sql = (
            f"SELECT count(*) FROM {table} "
            f"WHERE {scope_col} IS NOT NULL AND {scope_col} <> :a"
        )
    else:
        sql = f"SELECT count(*) FROM {table} WHERE {scope_col} <> :a"
    rows = await probe(
        rls_world, ROLE_APP, sql,
        tenant=rls_world.tenant_a, user=rls_world.owner_a,
        params={"a": rls_world.tenant_a},
    )
    assert rows[0][0] == 0, f"{table} leaked a foreign tenant's rows to tenant A"


@pytest.mark.parametrize("table", SENSITIVE)
async def test_unset_blank_and_malformed_scope_all_read_nothing(rls_world, table) -> None:
    """The three ways a scope can be absent are one answer: nothing.

    Before Batch 5 the first of these returned every tenant's rows, and the
    third raised 22P02 on the ``::uuid`` cast, which the API surfaced as a 500.
    """
    for scope in (None, "", "not-a-uuid", "'; DROP TABLE customers; --"):
        assert await count(rls_world, ROLE_APP, table, tenant=scope) == 0, (
            f"{table} returned rows with scope={scope!r}"
        )


@pytest.mark.parametrize("table", SENSITIVE)
async def test_a_foreign_tenant_scope_reads_nothing_of_ours(rls_world, table) -> None:
    """Tenant B's scope must not reach tenant A's rows, and vice versa.

    Counting is not enough — both shops hold a row in each of these tables — so
    this asserts on OWNERSHIP of what came back, not on how much did.
    """
    col = "tenant_id"
    rows = await probe(
        rls_world, ROLE_APP,
        f"SELECT count(*) FROM {table} WHERE {col} = :a",
        tenant=rls_world.tenant_b, user=rls_world.owner_b,
        params={"a": rls_world.tenant_a},
    )
    assert rows[0][0] == 0, f"tenant B could read tenant A's {table}"


# ── writes ───────────────────────────────────────────────────────────────────

async def test_a_tenant_can_write_its_own_row(rls_world) -> None:
    # A positive case matters as much as the denials: a fail-closed policy that
    # also blocks the legitimate write is a broken product, not a secure one.
    ok = await denied(
        rls_world, ROLE_APP,
        "INSERT INTO customers "
        "(id, tenant_id, full_name, phone, passport_photo_urls, status, metadata) "
        "VALUES (:i, :t, 'New', '+998900000999', '{}', 'active', '{}'::jsonb)",
        tenant=rls_world.tenant_a,
        params={"i": new_uuid(), "t": rls_world.tenant_a},
    )
    assert ok is False, "a scoped tenant must still be able to insert its own row"


#: Every table whose rows carry a ``tenant_id`` a tenant session is judged by.
#: The write half of the matrix runs over ALL of them, not a chosen few — the
#: brief asks for every affected tenant table, and the tables nobody thought to
#: name are exactly the ones r94/r95/r99 were written to fix after the fact.
WRITE_TABLES = tables_in(
    Category.TENANT_STRICT, Category.TENANT_SELF, Category.TENANT_GLOBAL_READ,
)


@pytest.mark.parametrize("table", WRITE_TABLES)
async def test_no_update_reaches_another_tenant(rls_world, table) -> None:
    assert await denied(
        rls_world, ROLE_APP,
        f"UPDATE {table} SET tenant_id = :b WHERE tenant_id = :b",
        tenant=rls_world.tenant_a,
        params={"b": rls_world.tenant_b},
    ), f"tenant A updated tenant B's {table}"


@pytest.mark.parametrize("table", WRITE_TABLES)
async def test_no_delete_reaches_another_tenant(rls_world, table) -> None:
    assert await denied(
        rls_world, ROLE_APP, f"DELETE FROM {table} WHERE tenant_id = :b",
        tenant=rls_world.tenant_a, params={"b": rls_world.tenant_b},
    ), f"tenant A deleted tenant B's {table}"


@pytest.mark.parametrize("table", WRITE_TABLES)
async def test_no_insert_reaches_another_tenant(rls_world, table) -> None:
    """Clone a row this tenant CAN see into the other tenant. ``WITH CHECK`` is
    the only thing that can refuse it.

    The refusal has to come from row security specifically. A unique-constraint
    violation would look identical to a boundary test and would prove nothing,
    so the reason is asserted, not just the outcome.
    """
    seeded = await count(
        rls_world, ROLE_APP, table, tenant=rls_world.tenant_a,
    )
    refused, why = await refused_by_policy(
        rls_world, ROLE_APP, CLONE_INTO_OTHER_TENANT.format(table=table),
        tenant=rls_world.tenant_a,
        params={"a": rls_world.tenant_a, "b": rls_world.tenant_b},
    )
    assert refused, f"tenant A planted a row in tenant B's {table} ({why})"
    if seeded:
        # There WAS a row to clone, so the statement really reached WITH CHECK.
        assert why == "row-level security", (
            f"{table} had a row to clone but was refused by {why!r} — that "
            "refusal would survive the policy being deleted"
        )
    else:
        assert why == "no rows matched"


async def test_with_check_blocks_moving_a_row_to_another_tenant(rls_world) -> None:
    """The read filter alone would let A rewrite its own row INTO B."""
    assert await denied(
        rls_world, ROLE_APP,
        "UPDATE customers SET tenant_id = :b WHERE tenant_id = :a",
        tenant=rls_world.tenant_a,
        params={"a": rls_world.tenant_a, "b": rls_world.tenant_b},
    )


@pytest.mark.parametrize("table", SENSITIVE)
async def test_an_unscoped_session_cannot_write_anything(rls_world, table) -> None:
    assert await denied(
        rls_world, ROLE_APP, f"DELETE FROM {table}",
    ), f"an unscoped session deleted from {table}"
    assert await denied(
        rls_world, ROLE_APP, f"UPDATE {table} SET tenant_id = tenant_id",
    ), f"an unscoped session updated {table}"


# ── platform-owned NULL-tenant rows ──────────────────────────────────────────

async def test_a_null_tenant_row_is_invisible_to_a_tenant(rls_world) -> None:
    """``tenant_id IS NULL`` means PLATFORM, not "everybody"."""
    async with rls_world.engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO receipt_blobs (id, tenant_id, content_type, created_at, "
            "updated_at) VALUES (gen_random_uuid(), NULL, 'image/webp', now(), now())"
        ))
    try:
        rows = await probe(
            rls_world, ROLE_APP,
            "SELECT count(*) FROM receipt_blobs WHERE tenant_id IS NULL",
            tenant=rls_world.tenant_a,
        )
        assert rows[0][0] == 0
        seen = await probe(
            rls_world, ROLE_PLATFORM,
            "SELECT count(*) FROM receipt_blobs WHERE tenant_id IS NULL",
        )
        assert seen[0][0] == 1, "the platform must still see its own row"
    finally:
        async with rls_world.engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM receipt_blobs WHERE tenant_id IS NULL"))


# ── the customer, across shops ───────────────────────────────────────────────

async def test_a_verified_customer_sees_their_own_rows_and_only_those(rls_world) -> None:
    seen = await probe(
        rls_world, ROLE_CUSTOMER, "SELECT id, tenant_id FROM customers",
        user=rls_world.customer_user,
    )
    assert [str(r[0]) for r in seen] == [rls_world.customer_a]
    assert str(seen[0][1]) == rls_world.tenant_a

    contracts = await probe(
        rls_world, ROLE_CUSTOMER, "SELECT id FROM installments",
        user=rls_world.customer_user,
    )
    assert [str(r[0]) for r in contracts] == [rls_world.installment_a]

    payments = await probe(
        rls_world, ROLE_CUSTOMER, "SELECT id FROM payments",
        user=rls_world.customer_user,
    )
    assert [str(r[0]) for r in payments] == [rls_world.payment_a]

    shops = await probe(
        rls_world, ROLE_CUSTOMER, "SELECT id FROM tenants",
        user=rls_world.customer_user,
    )
    assert [str(r[0]) for r in shops] == [rls_world.tenant_a]


async def test_an_unverified_identity_sees_no_customer_data(rls_world) -> None:
    """A Telegram link is a pointer, not an authorization.

    ``app_verified_telegram_id()`` returns NULL for an account with no proved,
    eligible number, and every customer policy is keyed on it — so this is the
    database enforcing the same rule ``identity_is_provable`` states in Python.
    """
    for table in ("customers", "installments", "payments", "tenants"):
        assert await count(
            rls_world, ROLE_CUSTOMER, table, user=rls_world.unverified_user,
        ) == 0, f"an unverified account read {table}"


async def test_a_customer_with_no_identity_at_all_sees_nothing(rls_world) -> None:
    for table in ("customers", "installments", "payments", "notifications"):
        assert await count(rls_world, ROLE_CUSTOMER, table) == 0


async def test_a_customer_sees_only_their_own_notifications(rls_world) -> None:
    seen = await probe(
        rls_world, ROLE_CUSTOMER, "SELECT id FROM notifications",
        user=rls_world.customer_user,
    )
    assert [str(r[0]) for r in seen] == [rls_world.notification_id]
    assert await count(
        rls_world, ROLE_CUSTOMER, "notifications", user=rls_world.owner_b,
    ) == 0


async def test_a_customer_cannot_reach_a_shops_back_office(rls_world) -> None:
    """The shops they buy from are not shops they work at."""
    for table in ("tenant_users", "audit_log", "kyc_blobs", "invitations",
                  "tenant_topups", "products", "sms_messages"):
        assert await count(
            rls_world, ROLE_CUSTOMER, table, user=rls_world.customer_user,
        ) == 0, f"a customer read {table}"


# ── identity bootstrap ───────────────────────────────────────────────────────

async def test_the_identity_bootstrap_reaches_nothing_directly(rls_world) -> None:
    """It used to reach the whole ``users`` table. Now it reaches NOTHING.

    (Batch 5 independent-review correction.) ``rls_users_identity`` was
    ``USING (true) WITH CHECK (true)``, so this assertion used to read
    ``== 4`` — every account in the world, available to a request that had not
    authenticated anybody. The policy is gone; the three narrow
    ``rls_identity_*`` capability functions are the only way in, and they are
    exercised in ``tests/test_identity_bootstrap_capability.py``.
    """
    assert await count(rls_world, ROLE_IDENTITY, "users") == 0
    for table in ("customers", "installments", "payments", "invitations",
                  "kyc_blobs", "receipt_blobs", "audit_log"):
        if table == "invitations":
            # Reachable only with a capability id, never by listing.
            assert await count(rls_world, ROLE_IDENTITY, table) == 0
            continue
        assert await count(rls_world, ROLE_IDENTITY, table) == 0, (
            f"the login bootstrap could read {table}"
        )


async def test_an_unscoped_tenant_session_sees_only_its_own_user(rls_world) -> None:
    """What a route that forgot ``resolve_tenant`` actually gets."""
    rows = await probe(
        rls_world, ROLE_APP, "SELECT id FROM users", user=rls_world.owner_a,
    )
    assert [str(r[0]) for r in rows] == [rls_world.owner_a]


async def test_the_login_bootstrap_can_find_its_own_memberships(rls_world) -> None:
    """Without this branch nobody could sign in: the membership lookup happens
    before any tenant is known."""
    rows = await probe(
        rls_world, ROLE_APP, "SELECT tenant_id FROM tenant_users",
        user=rls_world.owner_a,
    )
    assert [str(r[0]) for r in rows] == [rls_world.tenant_a]


# ── capability records ───────────────────────────────────────────────────────

async def test_invitations_cannot_be_listed_or_reached_cross_tenant(rls_world) -> None:
    assert await count(rls_world, ROLE_APP, "invitations") == 0
    rows = await probe(
        rls_world, ROLE_APP, "SELECT id FROM invitations", tenant=rls_world.tenant_a,
    )
    assert [str(r[0]) for r in rows] == [rls_world.invitation_a]
    # A capability for A's invitation does not open B's.
    rows = await probe(
        rls_world, ROLE_APP, "SELECT id FROM invitations",
        invitation=rls_world.invitation_a,
    )
    assert [str(r[0]) for r in rows] == [rls_world.invitation_a]


async def test_the_invitation_resolver_returns_ids_and_never_rows(rls_world) -> None:
    from app.core import invite_codes

    rows = await probe(
        rls_world, ROLE_APP,
        "SELECT invitation_id, tenant_id FROM public.rls_resolve_invitation"
        "(:v, NULL)",
        params={"v": invite_codes.verifier(rls_world.invitation_verifier_a)},
    )
    assert len(rows) == 1
    assert str(rows[0][0]) == rls_world.invitation_a
    assert str(rows[0][1]) == rls_world.tenant_a
    # A credential nobody minted resolves to nothing — the same answer as a
    # credential for an invitation that never existed.
    empty = await probe(
        rls_world, ROLE_APP,
        "SELECT invitation_id FROM public.rls_resolve_invitation(:v, NULL)",
        params={"v": invite_codes.verifier("not-a-real-credential")},
    )
    assert empty == []


@pytest.mark.parametrize("table", ("kyc_blobs", "receipt_blobs", "media_files"))
async def test_blobs_cannot_be_read_cross_tenant(rls_world, table) -> None:
    rows = await probe(
        rls_world, ROLE_APP, f"SELECT count(*) FROM {table} WHERE tenant_id = :b",
        tenant=rls_world.tenant_a, params={"b": rls_world.tenant_b},
    )
    assert rows[0][0] == 0
    assert await count(rls_world, ROLE_APP, table) == 0


async def test_batch4_document_and_notice_protections_survive(rls_world) -> None:
    """Batch 4 made these two fail-closed by hand; Batch 5 must not undo it."""
    for table in ("document_deliveries", "payment_review_notices"):
        assert await count(rls_world, ROLE_APP, table) == 0
        rows = await probe(
            rls_world, ROLE_APP, f"SELECT count(*) FROM {table} WHERE tenant_id = :b",
            tenant=rls_world.tenant_a, params={"b": rls_world.tenant_b},
        )
        assert rows[0][0] == 0


# ── worker / platform / infrastructure ───────────────────────────────────────

async def test_worker_and_platform_keep_their_cross_tenant_reach(rls_world) -> None:
    for role in (ROLE_WORKER, ROLE_PLATFORM):
        assert await count(rls_world, role, "customers") == 2, role
        assert await count(rls_world, role, "installments") == 2, role


async def test_a_worker_can_narrow_itself_to_one_tenant(rls_world) -> None:
    """Cross-tenant reach is the role's, not a licence to ignore a scope."""
    assert await count(
        rls_world, ROLE_WORKER, "customers", tenant=rls_world.tenant_a,
    ) == 1


async def test_scheduler_tables_are_unreachable_from_a_request(rls_world) -> None:
    """r95 claimed these were denied. They were fully writable.

    A tenant request could delete the scheduler's lease row (``cron_locks``),
    which is the lock that stops two workers running the same job, or rewrite
    APScheduler's jobstore.
    """
    for table in ("cron_locks", "apscheduler_jobs"):
        for role in (ROLE_APP, ROLE_CUSTOMER, ROLE_IDENTITY):
            assert await denied(rls_world, role, f"SELECT 1 FROM {table}"), (
                f"{role} can read {table}"
            )
            assert await denied(rls_world, role, f"DELETE FROM {table}"), (
                f"{role} can delete from {table}"
            )
        # `denied` reads "no row came back" as refusal, which is right for a
        # write and wrong for a reachability probe on an empty table — so the
        # worker's access is checked with a count, which always returns a row.
        assert await count(rls_world, ROLE_WORKER, table) >= 0


async def test_the_alembic_version_stamp_is_not_writable_from_a_request(rls_world) -> None:
    for role in (ROLE_APP, ROLE_CUSTOMER, ROLE_IDENTITY, ROLE_WORKER):
        assert await denied(
            rls_world, role, "UPDATE alembic_version SET version_num = 'x'",
        ), f"{role} can rewrite the migration stamp"


# ── platform-only and reference data ─────────────────────────────────────────

async def test_platform_only_tables_are_unreachable_from_a_tenant(rls_world) -> None:
    for table in tables_in(Category.PLATFORM_ONLY):
        assert await count(
            rls_world, ROLE_APP, table, tenant=rls_world.tenant_a,
        ) == 0, f"a tenant read {table}"


async def test_reference_data_is_readable_and_not_writable(rls_world) -> None:
    for table in tables_in(Category.GLOBAL_READ):
        await count(rls_world, ROLE_APP, table, tenant=rls_world.tenant_a)  # no error
        assert await denied(
            rls_world, ROLE_APP, f"DELETE FROM {table}", tenant=rls_world.tenant_a,
        ), f"a tenant could delete from the shared {table}"


async def test_platform_settings_exposes_only_the_public_keys(rls_world) -> None:
    from app.core.rls_manifest import PUBLIC_SETTING_KEYS

    async with rls_world.engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO platform_settings (id, key, value, created_at, updated_at) "
            "VALUES (gen_random_uuid(), 'billing_cards', '{}'::jsonb, now(), now()), "
            "       (gen_random_uuid(), 'internal_secretish', '{}'::jsonb, now(), now()) "
            "ON CONFLICT (key) DO NOTHING"
        ))
    try:
        rows = await probe(
            rls_world, ROLE_APP, "SELECT key FROM platform_settings",
            tenant=rls_world.tenant_a,
        )
        keys = {r[0] for r in rows}
        assert keys <= set(PUBLIC_SETTING_KEYS)
        assert "internal_secretish" not in keys
    finally:
        async with rls_world.engine.begin() as conn:
            await conn.execute(text(
                "DELETE FROM platform_settings WHERE key = 'internal_secretish'"))


# ── the write matrix must not be vacuous ─────────────────────────────────────

#: Protected tables the seeded world leaves EMPTY for tenant A. A clone test
#: against an empty table is refused because there was nothing to clone, not
#: because the policy said no — so the set is named here rather than left to be
#: discovered, and the test below fails the moment it changes in either
#: direction.
#:
#: One table. ``tenant_staff_invitations`` is the only protected table with no
#: SQLAlchemy model (it is reached through raw SQL), so the fixture cannot
#: construct a row for it. Its READ isolation is still covered by the SELECT half
#: of the matrix; only the WITH CHECK half goes unexercised.
EMPTY_FOR_TENANT_A: frozenset[str] = frozenset({"tenant_staff_invitations"})


async def test_the_write_matrix_is_not_vacuous(rls_world) -> None:
    """Say out loud which tables the clone test genuinely exercised.

    An isolation assertion against an empty table passes for the wrong reason.
    That is not a hypothetical here: it is precisely why 46 fail-open policies
    survived a green suite for months. So the emptiness is measured, the exempt
    set is written down, and the tables that carry customer or money data are
    forbidden from being in it.
    """
    empty, exercised = set(), set()
    for table in WRITE_TABLES:
        n = await count(rls_world, ROLE_APP, table, tenant=rls_world.tenant_a)
        (exercised if n else empty).add(table)

    assert empty == EMPTY_FOR_TENANT_A, (
        "the seeded world changed; update EMPTY_FOR_TENANT_A deliberately.\n"
        f"  newly empty:     {sorted(empty - EMPTY_FOR_TENANT_A)}\n"
        f"  newly populated: {sorted(EMPTY_FOR_TENANT_A - empty)}"
    )
    assert set(SENSITIVE) <= exercised | {"payment_review_notices"}, (
        f"a sensitive table is unexercised: {sorted(set(SENSITIVE) - exercised)}"
    )
    assert len(exercised) >= 10, (
        f"only {len(exercised)} of {len(WRITE_TABLES)} tables have a row to "
        "clone — the write matrix has become mostly decorative"
    )
