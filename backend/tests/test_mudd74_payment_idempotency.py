"""MUDD-74 (H13 backend half) — POST /tenant/payments now honors the client's
``Idempotency-Key`` header.

The frontend has sent this header on every payment submission since MUDD-56;
the server silently ignored it, so a double-tap or a network retry wrote a
SECOND Payment row for the same money — double-counting the customer's
balance with no dedup anywhere in the stack.

Fix: the header lands in ``Payment.idempotency_key``; the endpoint pre-checks
(tenant_id, idempotency_key) under a per-(tenant, key) advisory lock and
returns the existing payment on replay. ``uq_payments_idem`` is the durable DB
backstop, mirroring the contract-creation idempotency key (F-12).

DB-backed: self-skips without TEST_DATABASE_URL (the standard integration
gate); the source-wiring guard always runs.
"""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker


def test_mudd74_model_and_endpoint_are_wired() -> None:
    """Source guards: the model declares the column + partial unique index and
    the endpoint reads the header and pre-checks before creating."""
    from pathlib import Path

    model_src = Path("app/models/payment.py").read_text()
    assert "idempotency_key" in model_src
    assert "uq_payments_idem" in model_src
    assert "idempotency_key IS NOT NULL" in model_src

    ep_src = Path("app/api/tenant/payments.py").read_text()
    assert 'alias="Idempotency-Key"' in ep_src
    assert "Payment.idempotency_key == idempotency_key" in ep_src


async def test_mudd74_unique_index_enforces_idempotency(migrated_db) -> None:
    """The DB rejects a second payment with the same (tenant_id, idempotency_key),
    and allows many NULL keys (the index is partial) — same shape as
    uq_installments_idem."""
    from app.models import Customer, Installment, Payment, Tenant
    from app.models.enums import InstallmentStatus, PaymentMethod, PaymentStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="mudd74-idx", name="T")
        s.add(t); await s.flush()
        c = Customer(tenant_id=t.id, full_name="Idem Payer", phone="+998900074001")
        s.add(c); await s.flush()
        inst = Installment(
            tenant_id=t.id, customer_id=c.id, currency="UZS",
            status=InstallmentStatus.active,
            total_amount=Decimal("1000000"), down_payment=Decimal("0"),
            principal=Decimal("1000000"), monthly_amount=Decimal("100000"),
            months=10, start_date=date(2026, 1, 1),
            paid_count=0, paid_amount=Decimal("0"),
        )
        s.add(inst); await s.flush()
        tid, cid, iid = t.id, c.id, inst.id
        await s.commit()

    def _pay(key):
        return Payment(
            tenant_id=tid, installment_id=iid, customer_id=cid, currency="UZS",
            amount=Decimal("100000"), method=PaymentMethod.cash,
            status=PaymentStatus.pending, idempotency_key=key,
        )

    async with Session() as s:
        s.add(_pay("dup-key")); await s.commit()

    async with Session() as s:
        s.add(_pay("dup-key"))
        try:
            await s.commit()
            raise AssertionError("duplicate (tenant, idempotency_key) must be rejected")
        except IntegrityError:
            pass

    async with Session() as s:
        s.add(_pay(None)); s.add(_pay(None))
        await s.commit()  # no raise: NULL keys never collide


async def _seed_tenant(engine, *, slug: str):
    from app.models import Customer, Installment, Tenant, TenantUser, User
    from app.models.enums import InstallmentStatus, TenantRole

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug=slug, name="T")
        u = User(telegram_id=hash(slug) % 1_000_000_000)
        s.add_all([t, u]); await s.flush()
        c = Customer(tenant_id=t.id, full_name="HTTP Payer", phone=f"+99890{abs(hash(slug)) % 10_000_000:07d}")
        s.add(c); await s.flush()
        s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner))
        inst = Installment(
            tenant_id=t.id, customer_id=c.id, currency="UZS",
            status=InstallmentStatus.active,
            total_amount=Decimal("1000000"), down_payment=Decimal("0"),
            principal=Decimal("1000000"), monthly_amount=Decimal("100000"),
            months=10, start_date=date(2026, 1, 1),
            paid_count=0, paid_amount=Decimal("0"),
        )
        s.add(inst); await s.flush()
        tid, uid, iid = t.id, u.id, inst.id
        await s.commit()
        return tid, uid, iid


async def test_mudd74_replayed_post_returns_same_payment(migrated_db) -> None:
    """Two sequential POSTs with the same Idempotency-Key return the SAME
    payment and create exactly one row; a third POST without a key creates a
    distinct payment."""
    import httpx
    from httpx import ASGITransport

    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import Payment

    tid, uid, iid = await _seed_tenant(migrated_db, slug="mudd74-replay")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    body = {"installment_id": str(iid), "amount": 100000, "method": "cash"}
    auth = {"Authorization": f"Bearer {issue_token(user_id=str(uid))}", "X-Tenant-Id": str(tid)}

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cl:
            r1 = await cl.post("/api/tenant/payments", headers={**auth, "Idempotency-Key": "pay-k1"}, json=body)
            r2 = await cl.post("/api/tenant/payments", headers={**auth, "Idempotency-Key": "pay-k1"}, json=body)
            r3 = await cl.post("/api/tenant/payments", headers=auth, json=body)  # no key -> distinct
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r1.status_code == 201, r1.text[:300]
    assert r2.status_code in (200, 201), r2.text[:300]
    assert r1.json()["id"] == r2.json()["id"]
    assert r3.status_code == 201 and r3.json()["id"] != r1.json()["id"]

    async with Session() as s:
        n_keyed = (await s.execute(
            select(func.count()).select_from(Payment).where(
                Payment.tenant_id == tid, Payment.idempotency_key == "pay-k1")
        )).scalar_one()
    assert n_keyed == 1, f"idempotency key must map to exactly one payment, got {n_keyed}"


async def test_mudd74_concurrent_double_submit_writes_one_payment(migrated_db) -> None:
    """The real double-tap: two requests carrying the SAME key arrive at the
    same time. The per-(tenant, key) advisory lock serializes them — the
    second waits for the first to commit, then reads it back instead of
    inserting a duplicate. Exactly one payment must exist for the key
    afterwards, regardless of which response object won the race."""
    import httpx
    from httpx import ASGITransport

    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import Payment

    tid, uid, iid = await _seed_tenant(migrated_db, slug="mudd74-concurrent")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    body = {"installment_id": str(iid), "amount": 250000, "method": "cash"}
    auth = {"Authorization": f"Bearer {issue_token(user_id=str(uid))}", "X-Tenant-Id": str(tid)}

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cl:
            r1, r2 = await asyncio.gather(
                cl.post("/api/tenant/payments", headers={**auth, "Idempotency-Key": "race-k1"}, json=body),
                cl.post("/api/tenant/payments", headers={**auth, "Idempotency-Key": "race-k1"}, json=body),
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r1.status_code in (200, 201), r1.text[:300]
    assert r2.status_code in (200, 201), r2.text[:300]
    assert r1.json()["id"] == r2.json()["id"], "concurrent replay must resolve to the SAME payment"

    async with Session() as s:
        n_keyed = (await s.execute(
            select(func.count()).select_from(Payment).where(
                Payment.tenant_id == tid, Payment.idempotency_key == "race-k1")
        )).scalar_one()
        total_amount = (await s.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.tenant_id == tid, Payment.idempotency_key == "race-k1")
        )).scalar_one()
    assert n_keyed == 1, f"concurrent double-submit must write exactly one payment, got {n_keyed}"
    assert total_amount == Decimal("250000.00"), f"money must be booked once, got {total_amount}"


async def test_mudd74_key_absent_behaves_exactly_as_before(migrated_db) -> None:
    """No regression for the common case: two POSTs with no Idempotency-Key at
    all create two independent payments, same as before this fix."""
    import httpx
    from httpx import ASGITransport

    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app

    tid, uid, iid = await _seed_tenant(migrated_db, slug="mudd74-nokey")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    body = {"installment_id": str(iid), "amount": 50000, "method": "cash"}
    auth = {"Authorization": f"Bearer {issue_token(user_id=str(uid))}", "X-Tenant-Id": str(tid)}

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cl:
            r1 = await cl.post("/api/tenant/payments", headers=auth, json=body)
            r2 = await cl.post("/api/tenant/payments", headers=auth, json=body)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
