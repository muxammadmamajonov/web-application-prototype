"""#818 — media_files: tenant-isolation RLS + the single-blob integrity CHECK.

``media_files`` holds payment-proof references (financial PII), so its RLS boundary
is security-critical — yet it was only DDL source-inspected (the migration test greps
for "ENABLE ROW LEVEL SECURITY"). These tests exercise the **live** policy on real
Postgres: a tenant reads/writes only its own rows, the ``WITH CHECK`` clause blocks a
cross-tenant insert, and a no-tenant-context (platform) connection sees everything.

Plus the ``ck_media_files_single_blob`` CHECK: a row can never hold both durable
fallback slots (``object_key`` AND ``data``) at once.

Harness: seed as the owner (RLS-bypassing) role, then re-query as the restricted
``muddato_app`` role scoped via ``_rls_set_local_sql`` — the same pattern as
``test_rls_isolation``.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker


async def _two_tenants(engine, prefix: str):
    from app.models import Tenant

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        ta = Tenant(slug=f"{prefix}-a", name=f"{prefix} A")
        tb = Tenant(slug=f"{prefix}-b", name=f"{prefix} B")
        s.add_all([ta, tb])
        await s.flush()
        ids = (ta.id, tb.id)
        await s.commit()
    return ids


def _media(tenant_id, **fields):
    from app.models import MediaFile

    return MediaFile(
        id=uuid4(), tenant_id=tenant_id, bot_owner="platform", status="stored",
        mime_type="image/webp", **fields,
    )


# ── RLS read isolation ───────────────────────────────────────────────────────

async def test_rls_blocks_cross_tenant_media_read(migrated_db) -> None:
    from app.db import _rls_set_local_sql

    a_id, b_id = await _two_tenants(migrated_db, "mf-read")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        s.add(_media(a_id, original_filename="a.webp", file_id="FA"))
        s.add(_media(b_id, original_filename="b.webp", file_id="FB"))
        await s.commit()

    async def _names_under(tid):
        async with migrated_db.begin() as conn:
            for sql, params in _rls_set_local_sql(str(tid)):
                await conn.execute(text(sql), params)
            rows = (await conn.execute(text(
                "SELECT original_filename FROM media_files "
                "WHERE original_filename IN ('a.webp', 'b.webp')"
            ))).fetchall()
        return {r[0] for r in rows}

    assert await _names_under(a_id) == {"a.webp"}     # A sees only its own
    assert await _names_under(b_id) == {"b.webp"}     # B sees only its own — no leak


# ── RLS write isolation (WITH CHECK) ─────────────────────────────────────────

async def test_rls_blocks_cross_tenant_media_insert(migrated_db) -> None:
    from app.db import _rls_set_local_sql

    a_id, b_id = await _two_tenants(migrated_db, "mf-write")
    with pytest.raises(Exception) as e:   # asyncpg InsufficientPrivilege → wrapped
        async with migrated_db.begin() as conn:
            for sql, params in _rls_set_local_sql(str(a_id)):
                await conn.execute(text(sql), params)
            # Under tenant A's context, write a row owned by tenant B → WITH CHECK rejects.
            await conn.execute(text(
                "INSERT INTO media_files (id, tenant_id, bot_owner, status, mime_type) "
                "VALUES (:id, :tid, 'platform', 'stored', 'image/webp')"
            ), {"id": str(uuid4()), "tid": str(b_id)})
    assert "row-level security" in str(e.value).lower()


# ── no tenant context sees NOTHING; background work says who it is ───────────

async def test_no_tenant_context_sees_no_media(migrated_db) -> None:
    """(Batch 5) Inverted assertion, deliberately.

    This test used to assert that ``muddato_app`` with no ``app.tenant_id`` saw
    BOTH tenants' media — r73's "IS NULL branch" — on the reasoning that platform
    and background jobs must not be blocked. The reasoning was right and the
    mechanism was wrong: "no tenant named" is also what a route that forgot
    ``resolve_tenant`` looks like, so the exemption for the scheduler was an
    exemption for every bug. Background work now declares itself with the
    ``muddato_worker`` role, which this test pins alongside the denial.
    """
    a_id, b_id = await _two_tenants(migrated_db, "mf-plat")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        s.add(_media(a_id, original_filename="pa.webp", file_id="FA"))
        s.add(_media(b_id, original_filename="pb.webp", file_id="FB"))
        await s.commit()

    probe = (
        "SELECT original_filename FROM media_files "
        "WHERE original_filename IN ('pa.webp', 'pb.webp')"
    )

    async with migrated_db.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE muddato_app"))
        unscoped = (await conn.execute(text(probe))).fetchall()
    assert unscoped == [], "an unscoped tenant session must see no media at all"

    async with migrated_db.begin() as conn:
        await conn.execute(text("SET LOCAL ROLE muddato_worker"))
        as_worker = (await conn.execute(text(probe))).fetchall()
    assert {r[0] for r in as_worker} == {"pa.webp", "pb.webp"}, (
        "background work keeps its cross-tenant reach, through an explicit role"
    )


# ── ck_media_files_single_blob — never both durable slots ────────────────────

async def test_single_blob_check_rejects_both_object_key_and_data(migrated_db) -> None:
    a_id, _b = await _two_tenants(migrated_db, "mf-ck")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    # both fallback slots populated → ck_media_files_single_blob violation
    with pytest.raises(IntegrityError):
        async with Session() as s:
            s.add(_media(a_id, object_key="media/x.webp", data=b"BYTES"))
            await s.commit()

    # exactly one (dual-write durable copy) → OK
    async with Session() as s:
        s.add(_media(a_id, object_key="media/y.webp"))
        await s.commit()

    # neither (telegram_only keeps references only) → OK
    async with Session() as s:
        s.add(_media(a_id))
        await s.commit()
