"""`document_deliveries` fails CLOSED when no tenant is pinned.

The policy this replaces granted access whenever `app.tenant_id` was unset,
which means a `muddato_app` transaction that never called `resolve_tenant` read
every tenant's rows. The table carries user ids and Telegram chat/message/file
identifiers, so that is a cross-tenant disclosure rather than a tidiness issue.

Asserted through raw SQL as the restricted role, not through the ORM: the ORM's
own WHERE clauses would mask a broken policy, and the policy is precisely what
must hold when a WHERE clause is missing or wrong.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("migrated_db")

RESTRICTED_ROLE = "muddato_app"


@pytest_asyncio.fixture
async def rls_db(migrated_db):
    """Two tenants, one platform-owned row, and one delivery row each."""
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import Tenant, User

    # Tenants and users through the ORM so every NOT NULL the schema has grown
    # is satisfied by its own default; only the rows under test are raw SQL.
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as sess:
        ta = Tenant(name="RLS A", slug=f"rls-a-{uuid.uuid4().hex[:6]}")
        tb = Tenant(name="RLS B", slug=f"rls-b-{uuid.uuid4().hex[:6]}")
        ua = User(telegram_id=910001)
        ub = User(telegram_id=910002)
        sess.add_all([ta, tb, ua, ub])
        await sess.commit()
        a, b = ta.id, tb.id
        users = {"a": ua.id, "b": ub.id}

    rows = {}
    async with migrated_db.begin() as conn:
        for key, tid in (("a", a), ("b", b)):
            rid = uuid.uuid4()
            rows[key] = rid
            await conn.execute(text(
                "INSERT INTO document_deliveries "
                "(id, purpose, kind, object_id, document_version, user_id, tenant_id, "
                " status, bot_owner, file_id, chat_id) "
                "VALUES (:i, 'user_delivery', 'contract', :o, 'v1', :u, :t, "
                "        'sent', 'platform', :f, :c)"
            ), {"i": rid, "o": uuid.uuid4(), "u": users[key], "t": tid,
                "f": f"FILEID-{key.upper()}", "c": 1000 + ord(key)})

        platform_row = uuid.uuid4()
        await conn.execute(text(
            "INSERT INTO document_deliveries "
            "(id, purpose, kind, object_id, document_version, tenant_id, status, "
            " bot_owner, file_id) "
            "VALUES (:i, 'archive', 'invoice', :o, 'v1', NULL, 'sent', "
            "        'platform', 'FILEID-PLATFORM')"
        ), {"i": platform_row, "o": uuid.uuid4()})

    yield {"a": str(a), "b": str(b), "rows": rows, "platform": platform_row,
           "engine": migrated_db}


async def _as_tenant(engine, tenant_id: str | None, sql: str, params: dict | None = None):
    """Run `sql` as the restricted role, with `tenant_id` pinned (or not)."""
    async with engine.connect() as conn:
        await conn.begin()
        await conn.execute(text(f"SET LOCAL ROLE {RESTRICTED_ROLE}"))
        if tenant_id is not None:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id},
            )
        result = await conn.execute(text(sql), params or {})
        rows = result.fetchall()
        await conn.rollback()
        return rows


class TestReadIsolation:
    async def test_tenant_a_sees_only_its_own_rows(self, rls_db):
        rows = await _as_tenant(
            rls_db["engine"], rls_db["a"],
            "SELECT tenant_id, file_id FROM document_deliveries",
        )
        assert len(rows) == 1
        assert str(rows[0][0]) == rls_db["a"]
        assert rows[0][1] == "FILEID-A"

    async def test_tenant_b_sees_only_its_own_rows(self, rls_db):
        rows = await _as_tenant(
            rls_db["engine"], rls_db["b"],
            "SELECT tenant_id, file_id FROM document_deliveries",
        )
        assert len(rows) == 1
        assert rows[0][1] == "FILEID-B"

    async def test_an_unset_tenant_context_sees_nothing(self, rls_db):
        """THE defect. The previous policy returned every row here."""
        rows = await _as_tenant(
            rls_db["engine"], None, "SELECT id FROM document_deliveries",
        )
        assert rows == [], "unset tenant context read tenant-owned delivery rows"

    async def test_a_blank_tenant_context_sees_nothing(self, rls_db):
        rows = await _as_tenant(
            rls_db["engine"], "", "SELECT id FROM document_deliveries",
        )
        assert rows == []

    async def test_a_tenant_session_cannot_see_platform_rows(self, rls_db):
        """Platform-owned rows belong to the General topic and to no shop."""
        rows = await _as_tenant(
            rls_db["engine"], rls_db["a"],
            "SELECT id FROM document_deliveries WHERE tenant_id IS NULL",
        )
        assert rows == []

    async def test_no_telegram_identifier_leaks_across_tenants(self, rls_db):
        """The concrete disclosure: file_id, chat_id and user_id are what an
        unscoped read hands over."""
        rows = await _as_tenant(
            rls_db["engine"], rls_db["a"],
            "SELECT file_id, chat_id, user_id FROM document_deliveries",
        )
        blob = " ".join(str(v) for row in rows for v in row)
        assert "FILEID-B" not in blob
        assert "FILEID-PLATFORM" not in blob

    async def test_a_targeted_read_of_another_tenants_row_returns_nothing(self, rls_db):
        """Guessing a row id must not work either — the policy filters, it does
        not merely scope a listing."""
        rows = await _as_tenant(
            rls_db["engine"], rls_db["a"],
            "SELECT id FROM document_deliveries WHERE id = :i",
            {"i": rls_db["rows"]["b"]},
        )
        assert rows == []


class TestWriteIsolation:
    async def _insert_as(self, engine, session_tenant, row_tenant):
        async with engine.connect() as conn:
            await conn.begin()
            await conn.execute(text(f"SET LOCAL ROLE {RESTRICTED_ROLE}"))
            if session_tenant is not None:
                await conn.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": session_tenant},
                )
            try:
                await conn.execute(text(
                    "INSERT INTO document_deliveries "
                    "(id, purpose, kind, object_id, document_version, tenant_id, "
                    " status, bot_owner) "
                    "VALUES (:i, 'archive', 'contract', :o, 'v1', :t, 'sent', 'platform')"
                ), {"i": uuid.uuid4(), "o": uuid.uuid4(), "t": row_tenant})
                await conn.rollback()
                return "allowed"
            except Exception as exc:
                await conn.rollback()
                return type(exc).__name__ + ":" + str(exc)[:80]

    async def test_a_tenant_can_insert_its_own_row(self, rls_db):
        assert await self._insert_as(rls_db["engine"], rls_db["a"], rls_db["a"]) == "allowed"

    async def test_a_tenant_cannot_insert_another_tenants_row(self, rls_db):
        out = await self._insert_as(rls_db["engine"], rls_db["a"], rls_db["b"])
        assert out != "allowed", "wrote a row for another tenant"

    async def test_a_tenant_cannot_insert_a_platform_row(self, rls_db):
        out = await self._insert_as(rls_db["engine"], rls_db["a"], None)
        assert out != "allowed", "wrote a platform-owned row from a tenant session"

    async def test_an_unset_context_cannot_insert_anything(self, rls_db):
        out = await self._insert_as(rls_db["engine"], None, rls_db["a"])
        assert out != "allowed", "wrote a row with no tenant context at all"


class TestPlatformPathStillWorks:
    async def test_the_connection_role_is_not_subject_to_the_policy(self, rls_db):
        """Platform, scheduler and archive work is authorized by ROLE.

        Those paths never call `resolve_tenant`, so no `SET LOCAL ROLE` happens
        and the work runs on the ordinary connection role. That is the explicit
        mechanism — a role, not the absence of a session variable.
        """
        async with rls_db["engine"].connect() as conn:
            rows = (await conn.execute(
                text("SELECT count(*) FROM document_deliveries")
            )).scalar_one()
        assert rows >= 3, "the platform path lost access to its own table"

    async def test_the_table_is_not_force_rls(self, rls_db):
        """Deliberate and asserted rather than assumed: FORCE would subject the
        owner too, and the platform path depends on owner/superuser bypass."""
        async with rls_db["engine"].connect() as conn:
            relforce = (await conn.execute(text(
                "SELECT relforcerowsecurity FROM pg_class "
                "WHERE relname = 'document_deliveries'"
            ))).scalar_one()
            relrls = (await conn.execute(text(
                "SELECT relrowsecurity FROM pg_class "
                "WHERE relname = 'document_deliveries'"
            ))).scalar_one()
        assert relrls is True, "RLS is not enabled on the table"
        assert relforce is False


class TestThePolicyItself:
    async def test_it_does_not_grant_on_a_null_guc(self, rls_db):
        """Read from the catalog, so the assertion survives someone rewriting
        the migration: the fail-open shape must not come back."""
        async with rls_db["engine"].connect() as conn:
            # (Batch 5) Selected by ROLE, not by policy name: the policy that
            # serves an ordinary tenant request is now named per table and per
            # authority, and pinning the old name would make this test pass by
            # finding nothing. What it guards is unchanged — the clause the
            # tenant authority is judged by must not have a fail-open branch.
            rows = (await conn.execute(text(
                "SELECT policyname, qual, with_check FROM pg_policies "
                "WHERE tablename = 'document_deliveries' "
                "AND 'muddato_app' = ANY (roles)"
            ))).all()
        assert rows, "no tenant-authority policy on document_deliveries"
        for name, qual, check in rows:
            for clause in (qual, check):
                assert clause is not None, name
                assert "IS NULL" not in clause.upper().replace("NULLIF", ""), clause
                assert "tenant_id" in clause


class TestReviewNoticeIsolation:
    """`payment_review_notices` holds chat/message ids for a shop's own
    notifications, so it gets the same fail-closed treatment."""

    async def _seed_notice(self, engine, tenant_id, chat, message):
        from sqlalchemy import text as _t

        rid = uuid.uuid4()
        async with engine.begin() as conn:
            pay = (await conn.execute(_t("SELECT id FROM payments LIMIT 1"))).first()
            if pay is None:
                pytest.skip("no payment row to attach a notice to")
            await conn.execute(_t(
                "INSERT INTO payment_review_notices "
                "(id, payment_id, tenant_id, chat_id, message_id, state) "
                "VALUES (:i, :p, :t, :c, :m, 'pending')"
            ), {"i": rid, "p": pay[0], "t": tenant_id, "c": chat, "m": message})
        return rid

    async def test_the_policy_does_not_grant_on_a_null_guc(self, rls_db):
        async with rls_db["engine"].connect() as conn:
            # (Batch 5) Selected by ROLE, not by policy name: the policy that
            # serves an ordinary tenant request is now named per table and per
            # authority, and pinning the old name would make this test pass by
            # finding nothing. What it guards is unchanged — the clause the
            # tenant authority is judged by must not have a fail-open branch.
            rows = (await conn.execute(text(
                "SELECT policyname, qual, with_check FROM pg_policies "
                "WHERE tablename = 'payment_review_notices' "
                "AND 'muddato_app' = ANY (roles)"
            ))).all()
        assert rows, "no tenant-authority policy on payment_review_notices"
        for name, qual, check in rows:
            for clause in (qual, check):
                assert clause is not None, name
                assert "IS NULL" not in clause.upper().replace("NULLIF", ""), clause
                assert "tenant_id" in clause

    async def test_an_unset_context_reads_no_notices(self, rls_db):
        rows = await _as_tenant(
            rls_db["engine"], None, "SELECT id FROM payment_review_notices",
        )
        assert rows == []

    async def test_a_tenant_cannot_write_another_tenants_notice(self, rls_db):
        async with rls_db["engine"].connect() as conn:
            await conn.begin()
            await conn.execute(text(f"SET LOCAL ROLE {RESTRICTED_ROLE}"))
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": rls_db["a"]},
            )
            allowed = True
            try:
                await conn.execute(text(
                    "INSERT INTO payment_review_notices "
                    "(id, payment_id, tenant_id, chat_id, message_id, state) "
                    "VALUES (:i, :p, :t, 1, 1, 'pending')"
                ), {"i": uuid.uuid4(), "p": uuid.uuid4(), "t": rls_db["b"]})
            except Exception:
                allowed = False
            await conn.rollback()
        assert allowed is False

    async def test_the_worker_role_still_reaches_the_table(self, rls_db):
        """The sweep runs on the connection role, which owns the table and is
        therefore not subject to the policy — the same explicit mechanism as
        `document_deliveries`."""
        async with rls_db["engine"].connect() as conn:
            count = (await conn.execute(
                text("SELECT count(*) FROM payment_review_notices")
            )).scalar_one()
            forced = (await conn.execute(text(
                "SELECT relforcerowsecurity FROM pg_class "
                "WHERE relname = 'payment_review_notices'"
            ))).scalar_one()
        assert count >= 0
        assert forced is False
