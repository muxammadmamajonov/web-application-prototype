"""(F-19) ``run_dunning`` and ``daily_owner_briefing`` must use SQL aggregates +
keyset batches instead of loading all tenants / all open-past-due invoices into
Python lists. These tests assert (a) correctness of the aggregate-driven dunning
ladder on a real DB, and (b) that the implementations use aggregates/set-ops
(a source guard, so a future refactor can't silently regress to in-memory loads).

DB-backed tests self-skip without TEST_DATABASE_URL via ``migrated_db``.
"""
from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# ── source guards (no DB) ─────────────────────────────────────────────────────

def test_run_dunning_uses_aggregates_not_inmemory_reduce() -> None:
    from app.services import billing

    src = inspect.getsource(billing.run_dunning)
    # Set-based flip + SQL MIN aggregate + keyset batch — not a full materialise.
    assert "update(Invoice)" in src, "step 1 must be a set-based UPDATE, not a per-row loop"
    assert "func.min(" in src, "oldest-due must be a SQL MIN aggregate"
    assert "_DUNNING_BATCH" in src, "the per-tenant ladder must be keyset-batched"
    # The old in-memory shape (loading every overdue invoice into a dict) is gone.
    assert "oldest_due: dict" not in src


def test_daily_owner_briefing_uses_count_sum_aggregates() -> None:
    from app.services import scheduler

    src = inspect.getsource(scheduler.daily_owner_briefing_job)
    assert "func.count(" in src, "due-today / overdue must use COUNT, not len(list)"
    assert "_iter_keyset(" in src, "tenants must be keyset-batched, not loaded whole"
    # The old per-tenant list loads (then len()/sum()) are gone.
    assert "due_today = (await db.execute(" not in src
    assert "len(due_today)" not in src


# ── behaviour (DB-backed) ─────────────────────────────────────────────────────

def _inv(tid, i: int, *, status, due_at):
    from app.models import Invoice

    return Invoice(
        tenant_id=tid, plan_code="lite", invoice_number=f"2026-{i:06d}",
        period_start=date(2026, ((i - 1) % 12) + 1, 1),
        period_end=date(2026, ((i - 1) % 12) + 1, 28),
        active_installments_count=0, amount=Decimal("10000"),
        status=status, line_items={}, issued_at=datetime.now(UTC), due_at=due_at,
    )


async def test_run_dunning_flips_and_ladders_via_aggregates(migrated_db) -> None:
    from app.models import Invoice, Tenant
    from app.models.enums import InvoiceStatus, TenantStatus
    from app.services.billing import (
        BLOCK_AFTER_DUE_DAYS,
        SUSPEND_AFTER_DUE_DAYS,
        run_dunning,
    )

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    now = datetime.now(UTC)

    async with Session() as s:
        # t_open: an OPEN invoice already past due → should flip to overdue (and, being
        # > SUSPEND days overdue with an active tenant, suspend it).
        t_open = Tenant(slug="f19-open", name="open", plan_code="lite", status=TenantStatus.active)
        # t_block: OVERDUE invoice well past the block threshold → tenant blocked.
        t_block = Tenant(slug="f19-block", name="block", plan_code="lite", status=TenantStatus.active)
        # t_fresh: OPEN invoice NOT yet due → untouched.
        t_fresh = Tenant(slug="f19-fresh", name="fresh", plan_code="lite", status=TenantStatus.active)
        s.add_all([t_open, t_block, t_fresh])
        await s.flush()
        s.add_all([
            _inv(t_open.id, 1, status=InvoiceStatus.open,
                 due_at=now - timedelta(days=SUSPEND_AFTER_DUE_DAYS + 1)),
            _inv(t_block.id, 2, status=InvoiceStatus.overdue,
                 due_at=now - timedelta(days=BLOCK_AFTER_DUE_DAYS + 1)),
            _inv(t_fresh.id, 3, status=InvoiceStatus.open, due_at=now + timedelta(days=3)),
        ])
        await s.commit()
        ids = (t_open.id, t_block.id, t_fresh.id)

    async with Session() as s:
        summary = await run_dunning(s, now=now)
        await s.commit()

    # One open invoice flipped to overdue (t_open); t_fresh's open invoice stays open.
    assert summary["flipped_overdue"] == 1, summary
    assert summary["blocked"] == 1, summary
    assert summary["suspended"] == 1, summary

    async with Session() as s:
        statuses = {}
        for tid in ids:
            statuses[tid] = (await s.execute(
                select(Tenant.status).where(Tenant.id == tid)
            )).scalar_one()
        st_open, st_block, st_fresh = statuses[ids[0]], statuses[ids[1]], statuses[ids[2]]
        fresh_inv_status = (await s.execute(
            select(Invoice.status).where(Invoice.tenant_id == ids[2])
        )).scalar_one()

    assert st_open == TenantStatus.suspended
    assert st_block == TenantStatus.blocked
    assert st_fresh == TenantStatus.active
    assert fresh_inv_status == InvoiceStatus.open, "a not-yet-due invoice must not flip"


async def test_run_dunning_idempotent(migrated_db) -> None:
    """Second run flips nothing more (the open invoices are already overdue)."""
    from app.models import Tenant
    from app.models.enums import InvoiceStatus, TenantStatus
    from app.services.billing import run_dunning

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    now = datetime.now(UTC)
    async with Session() as s:
        t = Tenant(slug="f19-idem", name="idem", plan_code="lite", status=TenantStatus.active)
        s.add(t)
        await s.flush()
        s.add(_inv(t.id, 5, status=InvoiceStatus.open, due_at=now - timedelta(days=1)))
        await s.commit()

    async with Session() as s:
        first = await run_dunning(s, now=now)
        await s.commit()
    async with Session() as s:
        second = await run_dunning(s, now=now)
        await s.commit()

    assert first["flipped_overdue"] == 1
    assert second["flipped_overdue"] == 0, "nothing left open+past-due to flip"
