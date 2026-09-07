"""(F-09) The reminder/SMS dedup is backed by a PARTIAL UNIQUE INDEX
(``uq_sms_reminder_dedup``) so the INSERT collision — not a racy read-then-write —
is the idempotency guard. Two overlapping cron runs (a lease that lapsed mid-job)
must not double-send (double-charge) an SMS.

These build the schema directly from the ORM metadata (``Base.metadata.create_all``)
rather than via ``migrated_db``: the partial unique index lives on the model and the
matching Alembic migration is authored centrally (single linear head), so this proves
the model-level index behaviour independently of migration timing. Self-skips without
TEST_DATABASE_URL, same as the other DB-backed tiers.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _pg_url() -> str | None:
    import os

    return os.environ.get("TEST_DATABASE_URL")


@pytest_asyncio.fixture
async def schema_db():
    """Ephemeral schema built from the ORM metadata (so it includes the F-09 partial
    unique index defined on the model). Mirrors ``conftest.isolation_ctx``'s engine
    setup; self-skips without a reachable test Postgres."""
    url = _pg_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    from app.db import Base

    engine = create_async_engine(url)
    # (QA-R-002) DB tier is mandatory once TEST_DATABASE_URL is set — setup errors
    # (refused connection, privilege, bad schema) must FAIL, never self-skip green.
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        try:
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
        finally:
            await engine.dispose()


async def _tenant_customer_inst(Session, slug: str):
    from app.models import Customer, Installment, Tenant
    from app.models.enums import InstallmentStatus

    today = date.today()
    async with Session() as s:
        t = Tenant(slug=slug, name=slug)
        s.add(t)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="SMS Payer", phone="+998901112233")
        s.add(c)
        await s.flush()
        inst = Installment(currency="UZS",
            tenant_id=t.id, customer_id=c.id,
            total_amount=Decimal("500000"), principal=Decimal("500000"),
            down_payment=Decimal("0"), months=6, monthly_amount=Decimal("100000"),
            paid_amount=Decimal("0"), start_date=today - timedelta(days=10),
            status=InstallmentStatus.overdue, next_due_date=today - timedelta(days=2),
        )
        s.add(inst)
        await s.flush()
        out = (t.id, c.id, inst.id)
        await s.commit()
    return out


def _sms(tid, cid, iid, *, status, dedup_day, kind="reminder.overdue"):
    from app.models import SmsMessage

    return SmsMessage(
        tenant_id=tid, customer_id=cid, installment_id=iid, kind=kind,
        phone="+998901112233", text="reminder", parts=1, cost_uzs=Decimal("45"),
        status=status, dedup_day=dedup_day,
    )


async def test_unique_index_blocks_second_same_day_sent(schema_db) -> None:
    from app.models.enums import SmsStatus

    Session = async_sessionmaker(schema_db, expire_on_commit=False)
    tid, cid, iid = await _tenant_customer_inst(Session, "f09-dup")
    today = date.today()

    async with Session() as s:
        s.add(_sms(tid, cid, iid, status=SmsStatus.sent, dedup_day=today))
        await s.commit()

    with pytest.raises(IntegrityError):
        async with Session() as s:
            s.add(_sms(tid, cid, iid, status=SmsStatus.pending, dedup_day=today))
            await s.commit()


async def test_failed_send_does_not_block_retry(schema_db) -> None:
    """A FAILED row is outside the partial index (status IN ('sent','pending')), so a
    genuine retry the same day is allowed."""
    from app.models import SmsMessage
    from app.models.enums import SmsStatus

    Session = async_sessionmaker(schema_db, expire_on_commit=False)
    tid, cid, iid = await _tenant_customer_inst(Session, "f09-failed")
    today = date.today()

    async with Session() as s:
        s.add(_sms(tid, cid, iid, status=SmsStatus.failed, dedup_day=today))
        await s.commit()
    # Retry → a sent row for the same day must be accepted (no collision with failed).
    async with Session() as s:
        s.add(_sms(tid, cid, iid, status=SmsStatus.sent, dedup_day=today))
        await s.commit()

    async with Session() as s:
        n = (await s.execute(
            select(func.count(SmsMessage.id)).where(SmsMessage.installment_id == iid)
        )).scalar_one()
    assert n == 2


async def test_null_dedup_day_rows_never_collide(schema_db) -> None:
    """Manual / non-cron sends leave dedup_day NULL → excluded from the partial index,
    so many of them coexist (the index must not regress normal manual SMS)."""
    from app.models import SmsMessage
    from app.models.enums import SmsStatus

    Session = async_sessionmaker(schema_db, expire_on_commit=False)
    tid, cid, iid = await _tenant_customer_inst(Session, "f09-null")

    async with Session() as s:
        s.add_all([
            _sms(tid, cid, iid, status=SmsStatus.sent, dedup_day=None, kind="manual"),
            _sms(tid, cid, iid, status=SmsStatus.sent, dedup_day=None, kind="manual"),
            _sms(tid, cid, iid, status=SmsStatus.sent, dedup_day=None, kind="manual"),
        ])
        await s.commit()
    async with Session() as s:
        n = (await s.execute(
            select(func.count(SmsMessage.id)).where(SmsMessage.installment_id == iid)
        )).scalar_one()
    assert n == 3


async def test_try_sms_fallback_double_call_sends_once(schema_db, monkeypatch) -> None:
    """End-to-end: two _try_sms_fallback calls for the same (installment, kind, day) —
    modelling two overlapping cron runs — must call the provider only ONCE; the second
    reserve collides on the unique index and is swallowed (returns False)."""
    from app.models import Customer, Installment, TenantSettings
    from app.services import scheduler, sms_eskiz
    from app.services.sms_eskiz import SmsResult

    Session = async_sessionmaker(schema_db, expire_on_commit=False)
    tid, cid, iid = await _tenant_customer_inst(Session, "f09-e2e")

    async with Session() as s:
        s.add(TenantSettings(tenant_id=tid, sms_budget_uzs_per_month=1_000_000))
        await s.commit()

    sends: list[str] = []

    async def _fake_send(phone, text, *, sender=None):
        sends.append(phone)
        return SmsResult(ok=True, parts=1, cost_uzs=45, provider_message_id="x1")

    # Make the provider "configured" and deterministic.
    monkeypatch.setattr(sms_eskiz, "is_configured", lambda: True)
    monkeypatch.setattr(sms_eskiz, "send_sms", _fake_send)

    async with Session() as s:
        settings_row = (await s.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == tid)
        )).scalar_one()
        cust = (await s.execute(select(Customer).where(Customer.id == cid))).scalar_one()
        inst = (await s.execute(select(Installment).where(Installment.id == iid))).scalar_one()

        first = await scheduler._try_sms_fallback(s, settings_row, cust, inst, "overdue", "hi")
        second = await scheduler._try_sms_fallback(s, settings_row, cust, inst, "overdue", "hi")

    assert first is True, "first send should succeed"
    assert second is False, "second (duplicate) reserve must be swallowed → no send"
    assert len(sends) == 1, f"provider must be called exactly once, got {len(sends)}"
