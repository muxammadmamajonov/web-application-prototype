"""User segmentation for platform broadcasting.

The DB-free tests (registry + SQL-compiles) always run; the correctness test
seeds an ephemeral schema and runs in the integration tier (skips without PG).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from app.services import user_segments as seg
from tests.conftest import _pg_test_url


def test_segment_registry_is_consistent() -> None:
    assert frozenset(seg.SEGMENTS) == seg.VALID_SEGMENTS
    for uz, ru in seg.SEGMENTS.values():
        assert uz and ru          # both locales present


def test_every_segment_query_compiles() -> None:
    # Building the SQL must not raise for any known segment — catches a bad
    # column ref / join without needing a database.
    for key in seg.SEGMENTS:
        sql = str(seg.users_query(key, search="ali").compile())
        assert "users" in sql.lower()


def test_unknown_segment_raises() -> None:
    with pytest.raises(ValueError, match="unknown segment"):
        seg.segment_conditions("bogus")
    with pytest.raises(ValueError):
        seg.users_query("bogus")


@pytest_asyncio.fixture
async def seg_session():
    url = _pg_test_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — runs in the integration tier")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base

    engine = create_async_engine(url)
    # (QA-R-002) DB tier is mandatory once TEST_DATABASE_URL is set — setup errors
    # (refused connection, privilege, bad schema) must FAIL, never self-skip green.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield Session
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_segments_classify_users(seg_session) -> None:
    from app.models import Customer, Tenant, TenantUser, User
    from app.models.enums import PlatformRole, TenantRole, TenantStatus

    async with seg_session() as s:
        demo_t = Tenant(slug="demo1", name="Demo Shop", status=TenantStatus.demo)
        lapsed_t = Tenant(slug="lapsed1", name="Lapsed Shop", status=TenantStatus.suspended,
                          trial_ends_at=datetime(2020, 1, 1, tzinfo=UTC))
        active_t = Tenant(slug="active1", name="Active Shop", status=TenantStatus.active, plan_code="lite")
        s.add_all([demo_t, lapsed_t, active_t])
        await s.flush()

        client_u = User(telegram_id=1, locale="uz")        # is a customer (below)
        owner_demo = User(telegram_id=2, locale="ru")      # owner of the demo shop
        owner_lapsed = User(telegram_id=3)                 # owner of the lapsed-trial shop
        owner_active = User(telegram_id=4)                 # owner of the active shop
        admin_u = User(telegram_id=5, platform_role=PlatformRole.super_admin)
        inactive_u = User(telegram_id=6, is_active=False)  # excluded everywhere
        s.add_all([client_u, owner_demo, owner_lapsed, owner_active, admin_u, inactive_u])
        await s.flush()

        s.add_all([
            Customer(tenant_id=active_t.id, full_name="Client", phone="+998900000001", telegram_id=1),
            TenantUser(tenant_id=demo_t.id, user_id=owner_demo.id, role=TenantRole.owner),
            TenantUser(tenant_id=lapsed_t.id, user_id=owner_lapsed.id, role=TenantRole.owner),
            TenantUser(tenant_id=active_t.id, user_id=owner_active.id, role=TenantRole.owner),
        ])
        await s.commit()

        async def ids(segment: str) -> set[int]:
            return set(await seg.recipient_telegram_ids(s, segment))

        assert await ids("all") == {1, 2, 3, 4, 5}          # 6 excluded (inactive)
        assert await ids("clients") == {1}
        assert await ids("non_clients") == {2, 3, 4, 5}
        assert await ids("demo") == {2}
        assert await ids("demo_canceled") == {3}
        assert await ids("active_tenants") == {4}
        assert await ids("owners") == {2, 3, 4}
        assert await ids("staff") == set()
        assert await ids("admins") == {5}

        counts = await seg.segment_counts(s)
        assert counts["all"] == 5 and counts["clients"] == 1 and counts["demo"] == 1

        # locale-aware recipients: the ru owner gets ru.
        loc = dict(await seg.recipients_with_locale(s, "owners"))
        assert loc[2] == "ru" and loc[3] == "uz"
