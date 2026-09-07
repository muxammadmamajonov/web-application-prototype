"""MUDD-63 — three unlocked money races found in a Stage 3 remediation audit.

H3: ``replace_installment`` (backend/app/api/tenant/installments.py) did a
plain SELECT before its ``has_payments`` guard, unlike its neighbours
``cancel_installment`` / ``reschedule_installment`` in the same file, which
lock. A payment landing between the guard and the overwrite below it would be
silently discarded when the edit committed.

H4: ``execute_payoff`` (backend/app/api/me.py) wrote
``payoff_discount_amount`` under a plain SELECT while the payment webhooks
(payme.py / click.py) lock the same row via ``apply_payment()`` — a race
between "customer starts a payoff" and "a regular payment lands" could
overwrite one another's effect on the discount, or leave a stale discount on
an already-closed loan.

M1: ``payment_review_notify.finalize_due`` / ``retire_now_bg`` held their
claim's DB transaction open across the Telegram ``edit_message_text`` calls in
``finalize_claimed``, tying up a pool connection (and the claimed rows' locks)
for the duration of external I/O with no correctness benefit — the lease
(``state="finalizing"`` + ``lease_expires_at``) already protects a claimed
notice from a second worker once committed.

Reproduces each race under TRUE concurrency against a real Postgres
(``migrated_db``), following the same pattern as
``test_cancel_installment_race.py::test_cancel_respects_concurrent_completion``.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

NOW = datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


# ── H3: replace_installment ───────────────────────────────────────────────

def test_replace_installment_locks_the_row() -> None:
    """Source guard (mirrors test_cancel_installment_locks_the_row): the
    handler must SELECT ... FOR UPDATE so it serializes with the payment
    webhooks, same as its cancel/reschedule neighbours in this file."""
    from app.api.tenant.installments import replace_installment

    src = inspect.getsource(replace_installment)
    assert "with_for_update()" in src, "replace must lock the installment row (MUDD-63/H3)"


async def _seed_tenant_contract(engine, *, slug: str, telegram_id: int):
    from app.models import Customer, Installment, Tenant, TenantUser, User
    from app.models.enums import InstallmentStatus, TenantRole

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug=slug, name="Race T")
        s.add(t)
        await s.flush()
        u = User(telegram_id=telegram_id)
        s.add(u)
        await s.flush()
        tu = TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner, is_active=True)
        c = Customer(tenant_id=t.id, full_name="Race Payer", phone="+998900000444")
        s.add_all([tu, c])
        await s.flush()
        inst = Installment(currency="UZS",
            tenant_id=t.id, customer_id=c.id, total_amount=Decimal("1000000"),
            principal=Decimal("1000000"), months=10, monthly_amount=Decimal("100000"),
            start_date=date(2026, 1, 1), status=InstallmentStatus.active,
        )
        s.add(inst)
        await s.flush()
        await s.commit()
        return t, u, tu, c, str(inst.id)


async def test_replace_respects_a_payment_landing_mid_edit(migrated_db) -> None:
    """The real H3 race: a payment webhook credits the installment while an
    edit is in flight. With the row lock, replace blocks at its SELECT, then
    reads the FRESH paid_amount under the lock and 409s via the has_payments
    guard — the payment wins. Without the lock (the bug), replace reads
    paid_amount=0 before the payment lands, proceeds past the guard, and its
    commit silently discards the payment by overwriting the installment with
    the recomputed schedule.
    """
    from fastapi import HTTPException

    from app.api.tenant.installments import InstallmentIn, InstallmentItemIn, replace_installment
    from app.models import Installment

    t, u, tu, c, inst_id = await _seed_tenant_contract(migrated_db, slug="rx-t", telegram_id=771020)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    # A "webhook" holds the row lock and will credit a payment.
    holder = Session()
    held = (await holder.execute(
        select(Installment).where(Installment.id == UUID(inst_id)).with_for_update()
    )).scalar_one()

    body = InstallmentIn(
        customer_id=c.id, months=9,
        items=[InstallmentItemIn(product_name_snapshot="Phone XL",
                                 unit_price=Decimal("9000000"), qty=1)],
    )

    async def replace():
        async with Session() as s:
            return await replace_installment(
                iid=UUID(inst_id), body=body, user=u, ctx=(t, tu), db=s,
            )

    task = asyncio.create_task(replace())
    await asyncio.sleep(0.5)  # let replace reach its SELECT (blocks under the lock)
    held.paid_amount = Decimal("100000")
    held.paid_count = 1
    await holder.commit()  # webhook commits the payment + releases the lock
    await holder.close()

    outcome = (await asyncio.gather(task, return_exceptions=True))[0]
    async with Session() as s:
        final = await s.get(Installment, inst_id)

    # The payment wins: replace saw it under the lock and 409'd; it is NOT
    # silently wiped by the edit's overwrite.
    assert isinstance(outcome, HTTPException) and outcome.status_code == 409
    assert outcome.detail == "has_payments"
    assert final.paid_amount == Decimal("100000")
    assert final.months == 10  # the edit never applied


# ── H4: execute_payoff ────────────────────────────────────────────────────

def test_execute_payoff_locks_the_row() -> None:
    from app.api.me import execute_payoff

    src = inspect.getsource(execute_payoff)
    assert "with_for_update()" in src, "payoff must lock the installment row (MUDD-63/H4)"


async def _seed_payoff_customer(engine, *, slug: str, telegram_id: int, discount_pct="10"):
    from app.core.phone_identity import PhoneEligibility
    from app.models import Customer, Installment, Tenant, TenantSettings, User
    from app.models.enums import InstallmentStatus

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug=slug, name="Payoff T")
        s.add(t)
        await s.flush()
        ts = TenantSettings(tenant_id=t.id, early_payoff_discount_pct=Decimal(discount_pct))
        s.add(ts)
        # The customer's own account: proved phone ownership is what makes
        # _my_customer_rows()/identity_is_provable() resolve this installment
        # as theirs, without going through the full Telegram-contact proof flow.
        u = User(
            telegram_id=telegram_id, phone="+998900000555",
            verified_phone="+998900000555", verified_phone_at=NOW,
            verified_phone_source="telegram_contact",
            phone_eligibility=PhoneEligibility.eligible.value,
        )
        s.add(u)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="Payoff Customer",
                     phone="+998900000555", telegram_id=telegram_id)
        s.add(c)
        await s.flush()
        inst = Installment(currency="UZS",
            tenant_id=t.id, customer_id=c.id, total_amount=Decimal("1000000"),
            principal=Decimal("1000000"), months=10, monthly_amount=Decimal("100000"),
            paid_amount=Decimal("500000"), start_date=date(2026, 1, 1),
            status=InstallmentStatus.active,
        )
        s.add(inst)
        await s.flush()
        await s.commit()
        return t, u, c, str(inst.id)


async def test_payoff_does_not_leave_a_stale_discount_on_a_loan_settled_mid_flight(migrated_db) -> None:
    """The real H4 race: a regular payment that fully settles the loan lands
    (via a locked webhook path) WHILE a customer's payoff request is in
    flight. With the row lock, execute_payoff blocks at its SELECT, then
    re-checks status under the lock and skips writing the now-stale discount
    onto the already-completed loan. Without the lock (the bug), the write
    proceeds unconditionally and leaves ``payoff_discount_amount`` sitting on
    a closed contract for a later reversal/reopen to pick back up.
    """
    from app.api.me import PayoffExecuteIn, execute_payoff
    from app.models import Installment
    from app.models.enums import InstallmentStatus
    from app.services.installments import apply_payment

    _t, u, _c, inst_id = await _seed_payoff_customer(migrated_db, slug="px-t", telegram_id=771030)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    # A "webhook" holds the row lock and will apply a settling payment.
    holder = Session()
    held = (await holder.execute(
        select(Installment).where(Installment.id == UUID(inst_id)).with_for_update()
    )).scalar_one()

    async def payoff():
        async with Session() as s:
            return await execute_payoff(
                installment_id=UUID(inst_id), body=PayoffExecuteIn(method="cash"),
                user=u, db=s,
            )

    task = asyncio.create_task(payoff())
    await asyncio.sleep(0.5)  # let payoff compute its quote, then block on the lock
    apply_payment(held, Decimal("500000"), now=NOW)  # settles: 500000 + 500000 == principal
    await holder.commit()
    await holder.close()

    await asyncio.gather(task, return_exceptions=True)  # cash payoff may still 409 downstream; irrelevant here
    async with Session() as s:
        final = await s.get(Installment, inst_id)

    assert final.status == InstallmentStatus.completed
    assert final.payoff_discount_amount is None  # not clobbered by the in-flight payoff


# ── M1: payment_review_notify transaction scope ───────────────────────────

async def test_finalize_due_commits_the_claim_before_telegram_io(monkeypatch) -> None:
    """The transaction that claims due notices must NOT still be open while
    ``finalize_claimed`` is running the Telegram calls — that's what ties up a
    pool connection (and the claimed rows' lock) for the duration of external
    I/O. Fakes ``claim_due``/``finalize_claimed`` to record when each phase
    runs relative to ``db.commit()``.
    """
    from app.services import payment_review_notify as m

    calls: list[str] = []

    class _FakeDb:
        async def commit(self):
            calls.append("commit")

    async def _fake_claim_due(db, *, limit=25, now=None):
        calls.append("claim")
        return ["notice-1"]

    async def _fake_finalize_claimed(db, notices):
        # If the claim's transaction were still open, this call would be the
        # one doing the (simulated) slow Telegram I/O under that transaction.
        assert calls[-1] == "commit", "claim must be committed before finalize_claimed runs"
        calls.append("finalize")
        return len(notices)

    monkeypatch.setattr(m, "claim_due", _fake_claim_due)
    monkeypatch.setattr(m, "finalize_claimed", _fake_finalize_claimed)

    done = await m.finalize_due(_FakeDb(), limit=5)

    assert done == 1
    assert calls == ["claim", "commit", "finalize"]


async def test_retire_now_bg_commits_the_claim_before_telegram_io(monkeypatch) -> None:
    from uuid import uuid4

    from app.models import PaymentReviewNotice
    from app.services import payment_review_notify as m

    commits: list[int] = []
    finalize_seen_commits: list[int] = []

    class _FakeResult:
        def scalars(self):
            return self

        def all(self):
            return [PaymentReviewNotice(
                payment_id=uuid4(), tenant_id=uuid4(), chat_id=1, message_id=1, state="retry",
            )]

    class _FakeDb:
        async def execute(self, *a, **kw):
            return _FakeResult()

        async def flush(self):
            pass

        async def commit(self):
            commits.append(1)

    class _FakeSessionCtx:
        async def __aenter__(self):
            return _FakeDb()

        async def __aexit__(self, *exc):
            return False

    def _fake_worker_session():
        return _FakeSessionCtx()

    async def _fake_finalize_claimed(db, notices):
        # Assert the claim was already committed once (releasing the row
        # lock/pool connection) before this — the Telegram-call phase —  runs.
        finalize_seen_commits.append(len(commits))
        return len(notices)

    monkeypatch.setattr("app.db.worker_session", _fake_worker_session)
    monkeypatch.setattr(m, "finalize_claimed", _fake_finalize_claimed)

    await m.retire_now_bg(payment_id=uuid4())

    assert finalize_seen_commits == [1]  # one commit happened before finalize_claimed
    assert commits == [1, 1]  # claim commit, then the final persist commit
