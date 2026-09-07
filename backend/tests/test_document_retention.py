"""Retention boundaries for archived Telegram documents (Batch 6).

The finding this closes is "archived documents are never swept" — unbounded
growth of a private forum topic and of ``document_deliveries``. The risk in
fixing it is the opposite one: a sweep that deletes a copy something still
reads, or that deletes the row while leaving the file in the topic.

So these tests are written around the two questions that actually matter:
**which rows are unreachable** (nothing else may be touched) and **what happens
when the physical delete fails** (the row must survive to be retried).

DB-backed against the real schema. Telegram is stubbed at the service boundary —
these assert the retention decision, not the Bot API.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.services import document_retention
from app.services.document_retention import (
    find_orphaned,
    find_sweepable,
    sweep_archived_documents,
)

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=30)
RECENT = NOW - timedelta(hours=1)


@pytest_asyncio.fixture
async def retention_ctx():
    """Two tenants, each with a live installment to archive documents against."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base
    from app.models import Customer, Installment, Tenant, User
    from app.models.enums import InstallmentStatus

    engine = create_async_engine(url)
    # DROP SCHEMA rather than metadata.drop_all: the fail-closed RLS policies
    # reference OTHER tables (`users` is filtered through `tenant_users`), and
    # drop_all cannot order those drops — it fails with DependentObjectsStillExist
    # whenever a previous test left the migrated schema behind. Order-dependent,
    # so it is a flake rather than a failure, which is worse.
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with Session() as s:
            ta, tb = Tenant(slug="ret-a", name="A"), Tenant(slug="ret-b", name="B")
            s.add_all([ta, tb])
            await s.flush()
            ca = Customer(tenant_id=ta.id, full_name="A", phone="+998900007001")
            cb = Customer(tenant_id=tb.id, full_name="B", phone="+998900007002")
            s.add_all([ca, cb])
            await s.flush()

            def _inst(t, c):
                return Installment(
                    tenant_id=t, customer_id=c, currency="UZS",
                    total_amount=Decimal("100"), principal=Decimal("100"),
                    down_payment=Decimal("0"), months=1,
                    monthly_amount=Decimal("100"), paid_amount=Decimal("0"),
                    start_date=NOW.date(), status=InstallmentStatus.active,
                )

            ia, ib = _inst(ta.id, ca.id), _inst(tb.id, cb.id)
            recipient = User(telegram_id=970007001)
            s.add_all([ia, ib, recipient])
            await s.commit()
            ids = {
                "tenant_a": ta.id, "tenant_b": tb.id,
                "installment_a": ia.id, "installment_b": ib.id,
                "user": recipient.id,
            }
        yield Session, ids
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()


def _archive(*, object_id, tenant_id, version: str, created_at, status="sent",
             message_id: int | None = 500, kind: str = "contract"):
    from app.models import DocumentDelivery

    return DocumentDelivery(
        purpose="archive", kind=kind, object_id=object_id,
        document_version=version, tenant_id=tenant_id, status=status,
        chat_id=-100123 if status == "sent" else None,
        message_id=message_id if status == "sent" else None,
        message_thread_id=7 if status == "sent" else None,
        file_id="F" + version if status == "sent" else None,
        bot_owner="platform", created_at=created_at, updated_at=created_at,
    )


@pytest.fixture
def telegram_deletes(monkeypatch):
    """Record every delete the sweep asks for; answer success by default."""
    calls: list[tuple[int, int]] = []

    async def _delete(*, chat_id: int, message_id: int, bot=None) -> bool:
        calls.append((chat_id, message_id))
        return True

    import app.services.telegram_media as media

    monkeypatch.setattr(media, "delete_document_from_topic", _delete)
    return calls


@pytest.fixture
def telegram_delete_fails(monkeypatch):
    async def _delete(*, chat_id: int, message_id: int, bot=None) -> bool:
        return False

    import app.services.telegram_media as media

    monkeypatch.setattr(media, "delete_document_from_topic", _delete)


# ── which rows are unreachable ───────────────────────────────────────────────

class TestWhatIsSweepable:
    async def test_the_only_archive_of_a_live_object_is_never_swept(
        self, retention_ctx,
    ) -> None:
        """The assertion that keeps "deletion does not break live records" true."""
        Session, ids = retention_ctx
        async with Session() as s:
            s.add(_archive(object_id=ids["installment_a"],
                           tenant_id=ids["tenant_a"], version="v1", created_at=OLD))
            await s.commit()
            assert await find_sweepable(s, now=NOW) == []
            assert await find_orphaned(s, now=NOW) == []

    async def test_a_superseded_version_is_swept_and_the_newest_is_kept(
        self, retention_ctx,
    ) -> None:
        Session, ids = retention_ctx
        async with Session() as s:
            s.add_all([
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v1", created_at=OLD, message_id=501),
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v2", created_at=OLD + timedelta(days=1),
                         message_id=502),
            ])
            await s.commit()
            found = await find_sweepable(s, now=NOW)
            assert [r.document_version for r in found] == ["v1"], (
                "the newest archived version must survive — it is the one a cache "
                "lookup can still return"
            )

    async def test_a_row_inside_the_grace_period_is_left_alone(
        self, retention_ctx,
    ) -> None:
        """The boundary. A copy superseded moments ago may still be in flight."""
        Session, ids = retention_ctx
        async with Session() as s:
            s.add_all([
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v1", created_at=RECENT, message_id=501),
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v2", created_at=NOW, message_id=502),
            ])
            await s.commit()
            assert await find_sweepable(s, now=NOW) == []
            # …and the same rows once the grace period has elapsed.
            later = NOW + timedelta(days=8)
            assert [r.document_version for r in await find_sweepable(s, now=later)] == ["v1"]

    async def test_a_never_completed_upload_is_swept(self, retention_ctx) -> None:
        """A `failed`/`sending` claim row is already invisible to the cache."""
        Session, ids = retention_ctx
        async with Session() as s:
            s.add(_archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                           version="v1", created_at=OLD, status="failed"))
            await s.commit()
            assert len(await find_sweepable(s, now=NOW)) == 1

    async def test_an_archive_of_a_deleted_object_is_orphaned(
        self, retention_ctx,
    ) -> None:
        Session, ids = retention_ctx
        async with Session() as s:
            s.add(_archive(object_id=uuid4(), tenant_id=ids["tenant_a"],
                           version="v1", created_at=OLD))
            await s.commit()
            assert len(await find_orphaned(s, now=NOW)) == 1

    async def test_an_unmapped_kind_is_never_called_orphaned(
        self, retention_ctx,
    ) -> None:
        """"I do not know where to look" must not read as "the object is gone"."""
        Session, ids = retention_ctx
        async with Session() as s:
            s.add(_archive(object_id=uuid4(), tenant_id=ids["tenant_a"],
                           version="v1", created_at=OLD, kind="some_future_kind"))
            await s.commit()
            assert await find_orphaned(s, now=NOW) == []

    async def test_a_user_delivery_row_is_never_touched(self, retention_ctx) -> None:
        """This sweep is about the ARCHIVE. A record of what was sent to a person
        is not a cache and is not in scope."""

        Session, ids = retention_ctx
        async with Session() as s:
            row = _archive(object_id=ids["installment_a"],
                           tenant_id=ids["tenant_a"], version="v1", created_at=OLD)
            # ck_docdel_user_matches_purpose: a user delivery names its recipient.
            row.purpose = "user_delivery"
            row.user_id = ids["user"]
            s.add(row)
            await s.commit()
            assert await find_sweepable(s, now=NOW) == []
            assert await find_orphaned(s, now=NOW) == []


# ── the sweep itself ─────────────────────────────────────────────────────────

class TestTheSweep:
    async def test_it_deletes_the_message_before_the_row(
        self, retention_ctx, telegram_deletes,
    ) -> None:
        from app.models import DocumentDelivery

        Session, ids = retention_ctx
        async with Session() as s:
            s.add_all([
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v1", created_at=OLD, message_id=501),
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v2", created_at=OLD + timedelta(days=1),
                         message_id=502),
            ])
            await s.commit()
            result = await sweep_archived_documents(s, now=NOW)
            await s.commit()

            assert result.deleted == 1
            assert telegram_deletes == [(-100123, 501)], (
                "the superseded message must be removed from the topic, and only it"
            )
            left = (await s.execute(select(DocumentDelivery.document_version))).scalars().all()
            assert list(left) == ["v2"]

    async def test_a_failed_delete_keeps_the_row_for_retry(
        self, retention_ctx, telegram_delete_fails,
    ) -> None:
        """Metadata and storage must not drift apart. Dropping the row while the
        file survives would leave a document in the topic that nothing knows
        about — irrecoverable, because the row was the only record of it."""
        from app.models import DocumentDelivery

        Session, ids = retention_ctx
        async with Session() as s:
            s.add_all([
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v1", created_at=OLD, message_id=501),
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v2", created_at=OLD + timedelta(days=1),
                         message_id=502),
            ])
            await s.commit()
            result = await sweep_archived_documents(s, now=NOW)
            await s.commit()

            assert result.deleted == 0
            assert result.retained_failures == 1
            surviving = (await s.execute(
                select(func.count(DocumentDelivery.id)))).scalar_one()
            assert surviving == 2, "the row must survive so the next sweep retries"
            failed = (await s.execute(
                select(DocumentDelivery).where(
                    DocumentDelivery.document_version == "v1"))).scalar_one()
            assert failed.error == "retention_delete_failed", (
                "a failed cleanup must be observable, not silent"
            )

    async def test_the_sweep_is_idempotent(
        self, retention_ctx, telegram_deletes,
    ) -> None:
        Session, ids = retention_ctx
        async with Session() as s:
            s.add_all([
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v1", created_at=OLD, message_id=501),
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v2", created_at=OLD + timedelta(days=1),
                         message_id=502),
            ])
            await s.commit()
            first = await sweep_archived_documents(s, now=NOW)
            await s.commit()
            second = await sweep_archived_documents(s, now=NOW)
            await s.commit()

            assert first.deleted == 1
            assert second.deleted == 0 and second.examined == 0
            assert len(telegram_deletes) == 1, "a second sweep must not re-delete"

    async def test_a_retried_sweep_finishes_what_a_failure_left(
        self, retention_ctx, monkeypatch,
    ) -> None:
        """The recovery path: fail once, succeed on the next run."""
        from app.models import DocumentDelivery

        outcomes = iter([False, True])

        async def _delete(*, chat_id: int, message_id: int, bot=None) -> bool:
            return next(outcomes)

        import app.services.telegram_media as media

        monkeypatch.setattr(media, "delete_document_from_topic", _delete)

        Session, ids = retention_ctx
        async with Session() as s:
            s.add_all([
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v1", created_at=OLD, message_id=501),
                _archive(object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                         version="v2", created_at=OLD + timedelta(days=1),
                         message_id=502),
            ])
            await s.commit()
            assert (await sweep_archived_documents(s, now=NOW)).retained_failures == 1
            await s.commit()
            assert (await sweep_archived_documents(s, now=NOW)).deleted == 1
            await s.commit()
            left = (await s.execute(
                select(DocumentDelivery.document_version))).scalars().all()
            assert list(left) == ["v2"]

    async def test_one_tenants_sweep_does_not_touch_another(
        self, retention_ctx, telegram_deletes,
    ) -> None:
        """Both shops have a superseded copy. Each loses exactly its own."""
        from app.models import DocumentDelivery

        Session, ids = retention_ctx
        async with Session() as s:
            for tag, (inst, tenant, base) in enumerate((
                (ids["installment_a"], ids["tenant_a"], 600),
                (ids["installment_b"], ids["tenant_b"], 700),
            )):
                s.add_all([
                    _archive(object_id=inst, tenant_id=tenant, version=f"v1-{tag}",
                             created_at=OLD, message_id=base),
                    _archive(object_id=inst, tenant_id=tenant, version=f"v2-{tag}",
                             created_at=OLD + timedelta(days=1), message_id=base + 1),
                ])
            await s.commit()
            await sweep_archived_documents(s, now=NOW)
            await s.commit()

            rows = (await s.execute(
                select(DocumentDelivery.tenant_id, DocumentDelivery.document_version)
            )).all()
            by_tenant = {str(t): v for t, v in rows}
            assert by_tenant == {
                str(ids["tenant_a"]): "v2-0",
                str(ids["tenant_b"]): "v2-1",
            }, "each shop keeps its own newest copy and loses only its own stale one"

    async def test_the_batch_limit_bounds_one_run(self, retention_ctx) -> None:
        Session, ids = retention_ctx
        async with Session() as s:
            for i in range(6):
                s.add(_archive(
                    object_id=ids["installment_a"], tenant_id=ids["tenant_a"],
                    version=f"v{i}", created_at=OLD + timedelta(minutes=i),
                    message_id=800 + i,
                ))
            await s.commit()
            assert len(await find_sweepable(s, now=NOW, limit=2)) == 2


async def test_the_job_is_registered_on_the_scheduler() -> None:
    """A retention policy nothing runs is a document, not a policy."""
    import inspect

    from app.services import scheduler

    src = inspect.getsource(scheduler.setup_scheduler)
    assert "sweep_archived_documents" in src


def test_the_grace_period_is_not_a_legal_retention_duration() -> None:
    """Guard against this being quietly reinterpreted.

    docs/RETENTION.md's durations are a pending legal decision. This sweep is
    deliberately NOT that: it removes only rows no lookup can reach, so its
    window is operational slack and is safe to be short.
    """
    assert timedelta(days=30) >= document_retention.GRACE
