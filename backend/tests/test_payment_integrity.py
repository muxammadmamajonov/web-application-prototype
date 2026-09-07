"""Money-correctness + webhook idempotency over a REAL Postgres (integration).

The unit tier already covers the pure logic (test_installment_state: apply_payment
math; test_payment_webhooks: signing/parsing/cross-tenant). These prove the parts
the unit tests *can't*: that the database itself enforces the money invariants and
that the real Payme handlers are idempotent end-to-end against a live DB.

  Money correctness (#69):
    * Numeric(14,2) round-trips an accumulated balance with NO float drift.
    * the amounts_nonneg / down_le_total CHECK constraints actually reject bad
      writes (an app bug can't persist an impossible balance).
    * the tenant ledger (TenantBalanceTxn) stores Decimal exactly.

  Webhook auth + idempotency (#84 / Payme handshake):
    * the keyed blind-index lookup authenticates only the correct secret.
    * CreateTransaction is idempotent on external_ref (one Payment per tx id).
    * PerformTransaction applies the payment exactly ONCE — a Payme retry can't
      double-credit the installment.
    * CancelTransaction reverses a completed payment.

Skips without a reachable test Postgres (the CI `integration` job provides one;
locally, export TEST_DATABASE_URL). Uses the `migrated_db` fixture (real schema +
CHECK constraints), seeding via the ORM.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.enums import InstallmentStatus

NOW = datetime(2026, 6, 6, 12, 0, 0)


async def _seed_installment(
    engine, *, principal="1200000", monthly="100000", months=12,
    paid="0", total=None, down="0",
) -> tuple[str, str, str]:
    """A tenant + customer + one active installment. Returns (tenant, customer, inst) ids."""
    from app.models import Customer, Installment, Tenant

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="pay-t", name="Pay T")
        s.add(t)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="Payer One", phone="+998900000099")
        s.add(c)
        await s.flush()
        inst = Installment(currency="UZS",
            tenant_id=t.id, customer_id=c.id,
            total_amount=Decimal(total or principal), principal=Decimal(principal),
            down_payment=Decimal(down), months=months, monthly_amount=Decimal(monthly),
            paid_amount=Decimal(paid), start_date=date(2026, 1, 1),
            status=InstallmentStatus.active,
        )
        s.add(inst)
        await s.commit()
        return str(t.id), str(c.id), str(inst.id)


# ── money correctness (DB layer) ─────────────────────────────────────────────

async def test_paid_amount_roundtrips_exactly_through_numeric(migrated_db) -> None:
    """A sequence of awkward partial payments accumulates to an EXACT 2dp value
    and round-trips through Numeric(14,2) as a Decimal — no binary-float drift."""
    from app.models import Installment
    from app.services.installments import apply_payment

    _, _, inst_id = await _seed_installment(migrated_db, principal="1000000", monthly="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    # 0.10 + 0.20 + 33333.33 + 66666.37 = 100000.00 exactly (float would drift on 0.1+0.2)
    parts = [Decimal("0.10"), Decimal("0.20"), Decimal("33333.33"), Decimal("66666.37")]
    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        for p in parts:
            apply_payment(inst, p, now=NOW)
        await s.commit()
    async with Session() as s:  # reload from a fresh session → truly from the DB
        inst = await s.get(Installment, inst_id)
        assert isinstance(inst.paid_amount, Decimal)
        assert inst.paid_amount == Decimal("100000.00")
        assert inst.paid_amount == sum(parts)  # exact, not 99999.99999…


async def test_check_constraint_rejects_negative_paid_amount(migrated_db) -> None:
    from app.models import Installment

    _, _, inst_id = await _seed_installment(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    with pytest.raises(IntegrityError):
        async with Session() as s:
            inst = await s.get(Installment, inst_id)
            inst.paid_amount = Decimal("-1")  # amounts_nonneg must reject this
            await s.commit()


async def test_check_constraint_rejects_down_payment_over_total(migrated_db) -> None:
    from app.models import Installment

    _, _, inst_id = await _seed_installment(migrated_db, total="1000000", principal="1000000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    with pytest.raises(IntegrityError):
        async with Session() as s:
            inst = await s.get(Installment, inst_id)
            inst.down_payment = Decimal("2000000")  # down_le_total must reject this
            await s.commit()


async def test_tenant_balance_txn_stores_decimal_exactly(migrated_db) -> None:
    from app.models import Tenant, TenantBalanceTxn

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="led-t", name="Ledger T")
        s.add(t)
        await s.flush()
        s.add(TenantBalanceTxn(
            tenant_id=t.id, amount=Decimal("123456.78"), kind="topup",
            balance_after=Decimal("123456.78"),
        ))
        await s.commit()
        tid = str(t.id)
    async with Session() as s:
        txn = (await s.execute(
            select(TenantBalanceTxn).where(TenantBalanceTxn.tenant_id == tid)
        )).scalar_one()
        assert isinstance(txn.amount, Decimal)
        assert txn.amount == Decimal("123456.78")


# ── Payme webhook auth + idempotency (real handlers, real DB) ────────────────

async def _seed_payme(engine, *, secret="payme-secret-xyz", principal="1000000", monthly="100000"):
    from app.core.crypto import blind_index, encrypt_secret
    from app.models import Customer, Installment, Tenant, TenantSettings

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="pm-t", name="PM T")
        s.add(t)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="PM Payer", phone="+998900000088")
        s.add(c)
        await s.flush()
        inst = Installment(currency="UZS",
            tenant_id=t.id, customer_id=c.id, total_amount=Decimal(principal),
            principal=Decimal(principal), months=10, monthly_amount=Decimal(monthly),
            start_date=date(2026, 1, 1), status=InstallmentStatus.active,
        )
        s.add(inst)
        s.add(TenantSettings(
            tenant_id=t.id,
            payme_secret_key=encrypt_secret(secret),
            payme_secret_bidx=blind_index(secret, context="payme_secret"),
        ))
        await s.commit()
        return str(t.id), str(inst.id)


async def test_payme_auth_matches_only_the_correct_secret(migrated_db) -> None:
    # (Batch 5 independent-review correction) The lookup moved into
    # `core.webhook_identity`, behind an authority that holds no table
    # privilege, and the handler-side wrapper that verifies the secret and takes
    # the tenant scope is `payme._authenticate`. The property under test is
    # unchanged: only the right secret authenticates.
    from app.api.webhooks.payme import _authenticate

    tenant_id, _ = await _seed_payme(migrated_db, secret="right-secret")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        ok = await _authenticate(s, "right-secret")
        bad = await _authenticate(s, "wrong-secret")
        assert ok is not None and str(ok.tenant_id) == tenant_id
        assert bad is None  # blind-index miss + constant-time compare → no auth


async def test_payme_create_transaction_is_idempotent_on_external_ref(migrated_db) -> None:
    from app.api.webhooks.payme import _create_transaction
    from app.models import Payment

    _, inst_id = await _seed_payme(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    params = {"account": {"installment_id": inst_id}, "id": "tx-001", "amount": 100000_00, "time": 1}
    async with Session() as s:
        ts = await _load_ts(s)
        r1 = await _create_transaction(s, ts, 1, params)
        r2 = await _create_transaction(s, ts, 1, params)  # Payme retry, same tx id
        # Same transaction returned both times…
        assert r1["result"]["transaction"] == r2["result"]["transaction"]
        # …and exactly one Payment row exists for that external_ref.
        n = (await s.execute(
            select(func.count(Payment.id)).where(Payment.external_ref == "tx-001")
        )).scalar_one()
        assert n == 1


async def test_payme_perform_applies_payment_exactly_once(migrated_db) -> None:
    """The core anti-double-credit guarantee: a repeated PerformTransaction (a Payme
    retry) must not apply the amount twice to the installment balance."""
    from app.api.webhooks.payme import _create_transaction, _perform_transaction
    from app.models import Installment

    _, inst_id = await _seed_payme(migrated_db, principal="1000000", monthly="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    create = {"account": {"installment_id": inst_id}, "id": "tx-perf", "amount": 100000_00, "time": 1}
    async with Session() as s:
        ts = await _load_ts(s)
        await _create_transaction(s, ts, 1, create)
        r1 = await _perform_transaction(s, ts, 1, {"id": "tx-perf"})
        r2 = await _perform_transaction(s, ts, 1, {"id": "tx-perf"})  # retry
        assert r1["result"]["state"] == 2  # STATE_COMPLETED
        assert r2["result"]["perform_time"] == r1["result"]["perform_time"]  # idempotent echo
    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        assert inst.paid_amount == Decimal("100000")  # applied ONCE, not 200000


async def test_payme_pending_transaction_cannot_resurrect_cancelled_contract(migrated_db) -> None:
    """A contract may be cancelled after Payme creates but before it performs."""
    from app.api.webhooks.payme import _create_transaction, _perform_transaction
    from app.models import Installment, Payment
    from app.models.enums import PaymentStatus

    _, inst_id = await _seed_payme(migrated_db, principal="1000000", monthly="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    create = {"account": {"installment_id": inst_id}, "id": "tx-after-cancel",
              "amount": 100000_00, "time": 1}
    async with Session() as s:
        ts = await _load_ts(s)
        await _create_transaction(s, ts, 1, create)
        inst = await s.get(Installment, inst_id)
        inst.status = InstallmentStatus.cancelled
        await s.commit()
        result = await _perform_transaction(s, ts, 1, {"id": "tx-after-cancel"})
        assert result["error"]["message"] == "installment_closed"

    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        payment = (await s.execute(
            select(Payment).where(Payment.external_ref == "tx-after-cancel")
        )).scalar_one()
        assert inst.status is InstallmentStatus.cancelled
        assert inst.paid_amount == 0
        assert payment.status is PaymentStatus.pending


async def test_payme_pending_transaction_rechecks_remaining_balance_on_perform(migrated_db) -> None:
    """Other money may land after CreateTransaction; Perform must not overshoot."""
    from app.api.webhooks.payme import _create_transaction, _perform_transaction
    from app.models import Installment, Payment
    from app.models.enums import PaymentStatus
    from app.services.installments import apply_payment

    _, inst_id = await _seed_payme(migrated_db, principal="1000000", monthly="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    create = {"account": {"installment_id": inst_id}, "id": "tx-stale-balance",
              "amount": 600000_00, "time": 1}
    async with Session() as s:
        ts = await _load_ts(s)
        await _create_transaction(s, ts, 1, create)
        inst = await s.get(Installment, inst_id)
        apply_payment(inst, Decimal("500000"), now=NOW)
        await s.commit()
        result = await _perform_transaction(s, ts, 1, {"id": "tx-stale-balance"})
        assert result["error"]["message"] == "amount_invalid"

    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        payment = (await s.execute(
            select(Payment).where(Payment.external_ref == "tx-stale-balance")
        )).scalar_one()
        assert inst.paid_amount == Decimal("500000")
        assert payment.status is PaymentStatus.pending


async def test_payme_cancel_reverses_a_completed_payment(migrated_db) -> None:
    from app.api.webhooks.payme import (
        _cancel_transaction,
        _create_transaction,
        _perform_transaction,
    )
    from app.models import Installment

    _, inst_id = await _seed_payme(migrated_db, principal="1000000", monthly="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    create = {"account": {"installment_id": inst_id}, "id": "tx-cxl", "amount": 100000_00, "time": 1}
    async with Session() as s:
        ts = await _load_ts(s)
        await _create_transaction(s, ts, 1, create)
        await _perform_transaction(s, ts, 1, {"id": "tx-cxl"})
    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        assert inst.paid_amount == Decimal("100000")
    async with Session() as s:
        ts = await _load_ts(s)
        await _cancel_transaction(s, ts, 1, {"id": "tx-cxl", "reason": 1})
    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        assert inst.paid_amount == Decimal("0")  # credit reversed


# ── #117 Payme amount validation (real handlers, real DB) ────────────────────

async def test_payme_check_perform_rejects_over_outstanding_amount(migrated_db) -> None:
    from app.api.webhooks.payme import _check_perform

    _, inst_id = await _seed_payme(migrated_db, principal="1000000", monthly="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        ts = await _load_ts(s)
        # 2,000,000 UZS (tiyin) for a 1,000,000 loan → ERR_AMOUNT_INVALID (-31001)
        r = await _check_perform(s, ts, 1, {"account": {"installment_id": inst_id}, "amount": 2000000_00})
        assert r["error"]["code"] == -31001


async def test_payme_create_transaction_rejects_wrong_amount_and_creates_no_row(migrated_db) -> None:
    from app.api.webhooks.payme import _create_transaction
    from app.models import Payment

    _, inst_id = await _seed_payme(migrated_db, principal="1000000", monthly="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    params = {"account": {"installment_id": inst_id}, "id": "tx-bad-amt", "amount": 2000000_00, "time": 1}
    async with Session() as s:
        ts = await _load_ts(s)
        r = await _create_transaction(s, ts, 1, params)
        assert r["error"]["code"] == -31001
        n = (await s.execute(
            select(func.count(Payment.id)).where(Payment.external_ref == "tx-bad-amt")
        )).scalar_one()
        assert n == 0  # rejected amount must not create a Payment


async def test_payme_create_transaction_accepts_within_outstanding(migrated_db) -> None:
    from app.api.webhooks.payme import _create_transaction

    _, inst_id = await _seed_payme(migrated_db, principal="1000000", monthly="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    params = {"account": {"installment_id": inst_id}, "id": "tx-ok-amt", "amount": 100000_00, "time": 1}
    async with Session() as s:
        ts = await _load_ts(s)
        r = await _create_transaction(s, ts, 1, params)
        assert "result" in r and "error" not in r


# ── #121 late-fee accrual is NOT collected cash ──────────────────────────────

async def test_late_fee_payments_excluded_from_cashflow(migrated_db) -> None:
    """A late-fee accrual row must not be summed into the cashflow report — it is
    an accrual, not cash received."""
    from app.models import Payment
    from app.models.enums import PaymentMethod, PaymentStatus
    from app.services.reports import cashflow

    tid, cid, inst_id = await _seed_installment(migrated_db, principal="1000000", monthly="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        s.add(Payment(currency="UZS",   # real collected cash
            tenant_id=tid, installment_id=inst_id, customer_id=cid,
            amount=Decimal("100000"), method=PaymentMethod.cash,
            status=PaymentStatus.approved, approved_at=NOW,
        ))
        s.add(Payment(currency="UZS",   # accrued late fee — must be excluded from collected cash
            tenant_id=tid, installment_id=inst_id, customer_id=cid,
            amount=Decimal("50000"), method=PaymentMethod.late_fee,
            status=PaymentStatus.approved, approved_at=NOW, fee_date=NOW.date(),
        ))
        await s.commit()
        cf = await cashflow(s, tid, date_from=NOW.date(), date_to=NOW.date())
    assert cf["total"] == Decimal("100000")  # the 50,000 fee is NOT counted


async def test_late_fee_unique_per_installment_per_day(migrated_db) -> None:
    """A double cron-run can't charge two late fees for the same (installment, day)."""
    from app.models import Payment
    from app.models.enums import PaymentMethod, PaymentStatus

    tid, cid, inst_id = await _seed_installment(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    day = date(2026, 6, 6)

    async def _add_fee(s) -> None:
        s.add(Payment(currency="UZS",
            tenant_id=tid, installment_id=inst_id, customer_id=cid, amount=Decimal("10000"),
            method=PaymentMethod.late_fee, status=PaymentStatus.approved, fee_date=day,
        ))
        await s.commit()

    async with Session() as s:
        await _add_fee(s)
    with pytest.raises(IntegrityError):
        async with Session() as s:
            await _add_fee(s)


# ── #116 subscription top-up: no double-credit, amount-validated ──────────────

async def _seed_topup(engine, *, amount="100000", method="payme", status="pending"):
    from app.models import Tenant, TenantTopup

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="topup-t", name="Topup T", balance=Decimal("0"))
        s.add(t)
        await s.flush()
        tu = TenantTopup(tenant_id=t.id, amount=Decimal(amount), method=method, status=status)
        s.add(tu)
        await s.commit()
        return str(t.id), str(tu.id)


async def test_topup_concurrent_delivery_credits_once(migrated_db) -> None:
    """Two concurrent deliveries of the same provider callback must credit the
    tenant's prepaid balance exactly once (the row lock + status re-check)."""
    import asyncio

    from app.api.webhooks.subscription import _credit_by_topup_id
    from app.models import Tenant, TenantBalanceTxn

    tid, topup_id = await _seed_topup(migrated_db, amount="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async def deliver():
        async with Session() as s:
            return await _credit_by_topup_id(
                s, topup_id, provider_ref="px-1", captured_amount=Decimal("100000"),
            )

    await asyncio.gather(deliver(), deliver(), return_exceptions=True)

    async with Session() as s:
        t = await s.get(Tenant, tid)
        n = (await s.execute(
            select(func.count(TenantBalanceTxn.id)).where(TenantBalanceTxn.tenant_id == tid)
        )).scalar_one()
    assert t.balance == Decimal("100000")  # credited exactly once, not 200000
    assert n == 1


async def test_topup_rejects_short_captured_amount(migrated_db) -> None:
    """A tenant who requests a large top-up but pays less must NOT be credited
    the full requested amount."""
    from app.api.webhooks.subscription import _credit_by_topup_id
    from app.models import Tenant, TenantTopup

    tid, topup_id = await _seed_topup(migrated_db, amount="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        credited = await _credit_by_topup_id(
            s, topup_id, provider_ref="px-2", captured_amount=Decimal("50000"),
        )
    async with Session() as s:
        t = await s.get(Tenant, tid)
        tu = await s.get(TenantTopup, topup_id)
    assert credited is False
    assert t.balance == Decimal("0")   # nothing credited
    assert tu.status == "pending"      # not flipped to paid


async def test_topup_sequential_retry_credits_once(migrated_db) -> None:
    """(#733) A provider/Telegram REDELIVERY of the same top-up callback hours
    later — when the first delivery has already committed status='paid' — must not
    double-credit. This is the sequential analogue of the concurrent race above:
    here the second delivery reads the committed 'paid' status under its own lock
    and no-ops, so a $50 top-up retried can't become $100."""
    from app.api.webhooks.subscription import _credit_by_topup_id
    from app.models import Tenant, TenantBalanceTxn

    tid, topup_id = await _seed_topup(migrated_db, amount="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:  # first delivery — credits + commits status='paid'
        first = await _credit_by_topup_id(
            s, topup_id, provider_ref="rx-1", captured_amount=Decimal("100000"),
        )
    async with Session() as s:  # the retry, a fresh delivery after the first committed
        retry = await _credit_by_topup_id(
            s, topup_id, provider_ref="rx-1", captured_amount=Decimal("100000"),
        )

    assert first is True and retry is False  # credited once; the retry is a no-op
    async with Session() as s:
        t = await s.get(Tenant, tid)
        n = (await s.execute(
            select(func.count(TenantBalanceTxn.id)).where(TenantBalanceTxn.tenant_id == tid)
        )).scalar_one()
    assert t.balance == Decimal("100000")  # not 200000
    assert n == 1                          # exactly one ledger entry


# ── #173 webhook concurrency + branch coverage (real PG) ─────────────────────

async def test_payme_concurrent_perform_applies_once(migrated_db) -> None:
    """The core anti-double-credit invariant under TRUE concurrency: two
    overlapping PerformTransaction deliveries apply the amount exactly once.
    (If the with_for_update row lock were removed, this would credit twice.)"""
    import asyncio

    from app.api.webhooks.payme import _create_transaction, _perform_transaction
    from app.models import Installment

    _, inst_id = await _seed_payme(migrated_db, principal="1000000", monthly="100000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        ts = await _load_ts(s)
        await _create_transaction(
            s, ts, 1, {"account": {"installment_id": inst_id}, "id": "tx-c", "amount": 100000_00, "time": 1},
        )

    async def perform():
        async with Session() as s:
            ts = await _load_ts(s)
            return await _perform_transaction(s, ts, 1, {"id": "tx-c"})

    await asyncio.gather(perform(), perform(), return_exceptions=True)
    async with Session() as s:
        inst = await s.get(Installment, inst_id)
    assert inst.paid_amount == Decimal("100000")  # applied once, not 200000


class _FakeReq:
    def __init__(self, body: dict) -> None:
        self._body = body

    async def json(self) -> dict:
        return self._body


async def test_payme_rpc_rejects_unauthenticated(migrated_db) -> None:
    from app.api.webhooks.payme import payme_rpc

    await _seed_payme(migrated_db, secret="sek")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        r = await payme_rpc(
            _FakeReq({"id": 1, "method": "CheckPerformTransaction", "params": {}}), s, authorization=None,
        )
    assert r["error"]["code"] == -32504  # ERR_AUTH — dispatcher rejects no-auth


async def test_payme_rpc_unknown_method(migrated_db) -> None:
    import base64

    from app.api.webhooks.payme import payme_rpc

    await _seed_payme(migrated_db, secret="sek2")
    auth = "Basic " + base64.b64encode(b"Paycom:sek2").decode()
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        r = await payme_rpc(_FakeReq({"id": 2, "method": "NoSuchMethod", "params": {}}), s, authorization=auth)
    assert r["error"]["code"] == -32601  # ERR_METHOD_NOT_FOUND


async def _seed_click(engine, *, secret="click-sec", service_id="svc1", amount="100000.00"):
    from app.core.crypto import encrypt_secret
    from app.models import Customer, Installment, Payment, Tenant, TenantSettings
    from app.models.enums import PaymentMethod, PaymentStatus

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="cl-t", name="CL T")
        s.add(t)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="CL Payer", phone="+998900000077")
        s.add(c)
        await s.flush()
        inst = Installment(currency="UZS",
            tenant_id=t.id, customer_id=c.id, total_amount=Decimal(amount), principal=Decimal(amount),
            months=10, monthly_amount=Decimal("100000"), start_date=date(2026, 1, 1),
            status=InstallmentStatus.active,
        )
        s.add(inst)
        await s.flush()
        pay = Payment(currency="UZS",
            tenant_id=t.id, installment_id=inst.id, customer_id=c.id, amount=Decimal(amount),
            method=PaymentMethod.click, status=PaymentStatus.pending,
        )
        s.add(pay)
        await s.flush()
        s.add(TenantSettings(
            tenant_id=t.id, click_service_id=service_id, click_secret_key=encrypt_secret(secret),
        ))
        await s.commit()
        return str(inst.id), str(pay.id)


async def test_click_prepare_then_complete_applies_once(migrated_db) -> None:
    from app.api.webhooks.click import _click_sign, complete, prepare
    from app.models import Installment, Payment
    from app.models.enums import PaymentStatus

    secret, svc, amount, st = "click-sec", "svc1", "100000.00", "2026-06-06 12:00:00"
    inst_id, pay_id = await _seed_click(migrated_db, secret=secret, service_id=svc, amount=amount)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    psign = _click_sign(click_trans_id="ct1", service_id=svc, secret_key=secret,
                        merchant_trans_id=pay_id, amount=amount, action="0", sign_time=st)
    async with Session() as s:
        r = await prepare(click_trans_id="ct1", service_id=svc, click_paydoc_id="pd",
                          merchant_trans_id=pay_id, amount=amount, action="0", sign_time=st,
                          sign_string=psign, db=s)
    assert r["error"] == 0
    mpid = r["merchant_prepare_id"]

    csign = _click_sign(click_trans_id="ct1", service_id=svc, secret_key=secret,
                        merchant_trans_id=pay_id, merchant_prepare_id=mpid, amount=amount,
                        action="1", sign_time=st)

    async def do_complete():
        async with Session() as s:
            return await complete(click_trans_id="ct1", service_id=svc, merchant_trans_id=pay_id,
                                  merchant_prepare_id=mpid, amount=amount, action="1", error="0",
                                  error_note="OK", sign_time=st, sign_string=csign, db=s)

    c1 = await do_complete()
    c2 = await do_complete()  # idempotent retry
    # Click's documented contract: a Complete redelivered after the payment was
    # already confirmed replies with error -4 ("Already paid"), not another 0 —
    # see docs.click.uz merchant API, Complete error codes. ERR_OK on both calls
    # would be indistinguishable from two separate approvals on Click's side.
    assert c1["error"] == 0
    assert c2["error"] == -4
    async with Session() as s:
        pay = await s.get(Payment, pay_id)
        inst = await s.get(Installment, inst_id)
    assert pay.status == PaymentStatus.approved
    assert inst.paid_amount == Decimal("100000")  # applied once across the retry


async def test_click_concurrent_complete_applies_once(migrated_db) -> None:
    """The Click analogue of test_payme_concurrent_perform_applies_once (M-6,
    MUDD-70). Click retries a webhook exactly the way Payme does, and
    complete() takes the same two row locks (payment, installment) via
    .with_for_update() — this proves the anti-double-credit invariant holds
    for the SECOND provider rather than assuming it mirrors Payme untested."""
    import asyncio

    from app.api.webhooks.click import _click_sign, complete
    from app.models import Installment, Payment
    from app.models.enums import PaymentStatus

    secret, svc, amount, st = "click-sec", "svc1", "100000.00", "2026-06-06 12:00:00"
    inst_id, pay_id = await _seed_click(migrated_db, secret=secret, service_id=svc, amount=amount)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    sign = _click_sign(
        click_trans_id="ct-concurrent", service_id=svc, secret_key=secret,
        merchant_trans_id=pay_id, merchant_prepare_id=pay_id, amount=amount,
        action="1", sign_time=st,
    )

    async def do_complete():
        async with Session() as s:
            return await complete(
                click_trans_id="ct-concurrent", service_id=svc, merchant_trans_id=pay_id,
                merchant_prepare_id=pay_id, amount=amount, action="1", error="0",
                error_note="OK", sign_time=st, sign_string=sign, db=s,
            )

    await asyncio.gather(do_complete(), do_complete(), return_exceptions=True)
    async with Session() as s:
        pay = await s.get(Payment, pay_id)
        inst = await s.get(Installment, inst_id)
    assert pay.status == PaymentStatus.approved
    assert inst.paid_amount == Decimal("100000")  # applied once, not 200000


async def test_click_complete_cannot_resurrect_cancelled_contract(migrated_db) -> None:
    from app.api.webhooks.click import _click_sign, complete
    from app.models import Installment, Payment
    from app.models.enums import PaymentStatus

    secret, svc, amount, st = "click-sec", "svc1", "100000.00", "2026-06-06 12:00:00"
    inst_id, pay_id = await _seed_click(migrated_db, secret=secret, service_id=svc, amount=amount)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        inst.status = InstallmentStatus.cancelled
        await s.commit()

    sign = _click_sign(
        click_trans_id="ct-cancelled", service_id=svc, secret_key=secret,
        merchant_trans_id=pay_id, merchant_prepare_id=pay_id, amount=amount,
        action="1", sign_time=st,
    )
    async with Session() as s:
        result = await complete(
            click_trans_id="ct-cancelled", service_id=svc, merchant_trans_id=pay_id,
            merchant_prepare_id=pay_id, amount=amount, action="1", error="0",
            error_note="OK", sign_time=st, sign_string=sign, db=s,
        )
        assert result["error_note"] == "installment_closed"

    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        payment = await s.get(Payment, pay_id)
        assert inst.status is InstallmentStatus.cancelled
        assert inst.paid_amount == 0
        assert payment.status is PaymentStatus.pending


async def test_click_complete_rechecks_remaining_balance(migrated_db) -> None:
    from app.api.webhooks.click import ERR_BAD_AMOUNT, _click_sign, complete
    from app.models import Installment, Payment
    from app.models.enums import PaymentStatus
    from app.services.installments import apply_payment

    secret, svc, amount, st = "click-sec", "svc1", "600000.00", "2026-06-06 12:00:00"
    inst_id, pay_id = await _seed_click(migrated_db, secret=secret, service_id=svc, amount=amount)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        # _seed_click sets principal to the pending amount. Raise the contract
        # principal so the pending Click amount is initially valid, then let
        # another payment reduce the remaining balance below it.
        inst.principal = Decimal("1000000")
        inst.total_amount = Decimal("1000000")
        apply_payment(inst, Decimal("500000"), now=NOW)
        await s.commit()

    sign = _click_sign(
        click_trans_id="ct-stale", service_id=svc, secret_key=secret,
        merchant_trans_id=pay_id, merchant_prepare_id=pay_id, amount=amount,
        action="1", sign_time=st,
    )
    async with Session() as s:
        result = await complete(
            click_trans_id="ct-stale", service_id=svc, merchant_trans_id=pay_id,
            merchant_prepare_id=pay_id, amount=amount, action="1", error="0",
            error_note="OK", sign_time=st, sign_string=sign, db=s,
        )
        assert result["error"] == ERR_BAD_AMOUNT
        assert result["error_note"] == "amount_invalid"

    async with Session() as s:
        inst = await s.get(Installment, inst_id)
        payment = await s.get(Payment, pay_id)
        assert inst.paid_amount == Decimal("500000")
        assert payment.status is PaymentStatus.pending


async def test_click_prepare_rejects_amount_mismatch(migrated_db) -> None:
    from app.api.webhooks.click import _click_sign, prepare

    secret, svc, st = "click-sec", "svc1", "2026-06-06 12:00:00"
    _, pay_id = await _seed_click(migrated_db, secret=secret, service_id=svc, amount="100000.00")
    bad = "999999.00"
    psign = _click_sign(click_trans_id="ct2", service_id=svc, secret_key=secret,
                        merchant_trans_id=pay_id, amount=bad, action="0", sign_time=st)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        r = await prepare(click_trans_id="ct2", service_id=svc, click_paydoc_id="pd",
                          merchant_trans_id=pay_id, amount=bad, action="0", sign_time=st,
                          sign_string=psign, db=s)
    assert r["error"] == -2  # ERR_BAD_AMOUNT — wrong amount can't prepare


async def test_click_prepare_rejects_forged_signature(migrated_db) -> None:
    """(#748) A webhook signed with a key the caller doesn't have is rejected with
    ERR_SIGN_CHECK_FAILED and leaves the payment untouched — proving the
    constant-time HMAC gate actually fires on the endpoint, not just in _click_sign."""
    from app.api.webhooks.click import ERR_SIGN_CHECK_FAILED, _click_sign, prepare
    from app.models import Payment
    from app.models.enums import PaymentStatus

    secret, svc, amount, st = "click-sec", "svc1", "100000.00", "2026-06-06 12:00:00"
    _, pay_id = await _seed_click(migrated_db, secret=secret, service_id=svc, amount=amount)
    # Attacker forges a signature with a secret they don't actually hold.
    forged = _click_sign(click_trans_id="ctF", service_id=svc, secret_key="attacker-guess",
                         merchant_trans_id=pay_id, amount=amount, action="0", sign_time=st)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        r = await prepare(click_trans_id="ctF", service_id=svc, click_paydoc_id="pd",
                          merchant_trans_id=pay_id, amount=amount, action="0", sign_time=st,
                          sign_string=forged, db=s)
    assert r["error"] == ERR_SIGN_CHECK_FAILED  # -1, forged signature refused
    async with Session() as s:
        pay = await s.get(Payment, pay_id)
    assert pay.status == PaymentStatus.pending  # no prepare side effects


async def test_click_prepare_rejects_when_secret_decryption_fails(migrated_db, monkeypatch) -> None:
    """A ciphertext/decryption failure must not turn the merchant key into ''."""
    from app.api.webhooks import click

    svc, amount, st = "svc1", "100000.00", "2026-06-06 12:00:00"
    _, pay_id = await _seed_click(migrated_db, secret="click-sec", service_id=svc, amount=amount)
    monkeypatch.setattr(click, "decrypt_secret", lambda value: None)
    forged_with_empty = click._click_sign(
        click_trans_id="ct-empty", service_id=svc, secret_key="",
        merchant_trans_id=pay_id, amount=amount, action="0", sign_time=st,
    )
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        result = await click.prepare(
            click_trans_id="ct-empty", service_id=svc, click_paydoc_id="pd",
            merchant_trans_id=pay_id, amount=amount, action="0", sign_time=st,
            sign_string=forged_with_empty, db=s,
        )
    assert result["error"] == click.ERR_SIGN_CHECK_FAILED
    # (Batch 5 independent-review correction) One note for every authentication
    # failure — unknown service, unusable key, bad signature. The old
    # "service_unknown" told a caller which merchant ids exist, which is an
    # enumeration oracle on an unauthenticated endpoint.
    assert result["error_note"] == "sign_failed"


async def test_click_complete_rejects_forged_signature(migrated_db) -> None:
    """(#748) The signature gate fires on /complete BEFORE any money is applied:
    a forged complete is refused and the payment never reaches approved."""
    from app.api.webhooks.click import ERR_SIGN_CHECK_FAILED, _click_sign, complete
    from app.models import Payment
    from app.models.enums import PaymentStatus

    secret, svc, amount, st = "click-sec", "svc1", "100000.00", "2026-06-06 12:00:00"
    _, pay_id = await _seed_click(migrated_db, secret=secret, service_id=svc, amount=amount)
    forged = _click_sign(click_trans_id="ctF2", service_id=svc, secret_key="attacker-guess",
                         merchant_trans_id=pay_id, merchant_prepare_id=pay_id, amount=amount,
                         action="1", sign_time=st)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        r = await complete(click_trans_id="ctF2", service_id=svc, merchant_trans_id=pay_id,
                           merchant_prepare_id=pay_id, amount=amount, action="1", error="0",
                           error_note="OK", sign_time=st, sign_string=forged, db=s)
    assert r["error"] == ERR_SIGN_CHECK_FAILED  # refused before applying
    async with Session() as s:
        pay = await s.get(Payment, pay_id)
    assert pay.status == PaymentStatus.pending  # money never applied on a forgery


async def test_click_complete_user_cancel_refunds_without_applying(migrated_db) -> None:
    from app.api.webhooks.click import _click_sign, complete
    from app.models import Installment, Payment
    from app.models.enums import PaymentStatus

    secret, svc, amount, st = "click-sec", "svc1", "100000.00", "2026-06-06 12:00:00"
    inst_id, pay_id = await _seed_click(migrated_db, secret=secret, service_id=svc, amount=amount)
    csign = _click_sign(click_trans_id="ct3", service_id=svc, secret_key=secret,
                        merchant_trans_id=pay_id, merchant_prepare_id=pay_id, amount=amount,
                        action="1", sign_time=st)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        r = await complete(click_trans_id="ct3", service_id=svc, merchant_trans_id=pay_id,
                           merchant_prepare_id=pay_id, amount=amount, action="1", error="-1",
                           error_note="cancelled", sign_time=st, sign_string=csign, db=s)
    assert r["error"] == -9  # ERR_TRANSACTION_CANCELLED
    async with Session() as s:
        pay = await s.get(Payment, pay_id)
        inst = await s.get(Installment, inst_id)
    assert pay.status == PaymentStatus.refunded
    assert inst.paid_amount == Decimal("0")  # cancel applies nothing


async def test_click_cancel_after_approval_reverses_the_applied_money(migrated_db) -> None:
    from app.api.webhooks.click import _click_sign, complete
    from app.models import Installment, Payment
    from app.models.enums import PaymentStatus

    secret, svc, amount, st = "click-sec", "svc1", "100000.00", "2026-06-06 12:00:00"
    inst_id, pay_id = await _seed_click(migrated_db, secret=secret, service_id=svc, amount=amount)
    sign = _click_sign(
        click_trans_id="ct-reverse", service_id=svc, secret_key=secret,
        merchant_trans_id=pay_id, merchant_prepare_id=pay_id, amount=amount,
        action="1", sign_time=st,
    )
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        approved = await complete(
            click_trans_id="ct-reverse", service_id=svc, merchant_trans_id=pay_id,
            merchant_prepare_id=pay_id, amount=amount, action="1", error="0",
            error_note="OK", sign_time=st, sign_string=sign, db=s,
        )
        assert approved["error"] == 0
    async with Session() as s:
        assert (await s.get(Installment, inst_id)).paid_amount == Decimal("100000")

    async with Session() as s:
        cancelled = await complete(
            click_trans_id="ct-reverse", service_id=svc, merchant_trans_id=pay_id,
            merchant_prepare_id=pay_id, amount=amount, action="1", error="-9",
            error_note="cancelled", sign_time=st, sign_string=sign, db=s,
        )
        assert cancelled["error"] == -9
    async with Session() as s:
        payment = await s.get(Payment, pay_id)
        inst = await s.get(Installment, inst_id)
        assert payment.status is PaymentStatus.refunded
        assert inst.paid_amount == 0


async def _load_ts(session):
    from app.models import TenantSettings

    return (await session.execute(select(TenantSettings).limit(1))).scalar_one()
