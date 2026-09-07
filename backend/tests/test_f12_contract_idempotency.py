"""F-12 — server-side idempotency for contract creation.

The client sends a stable ``Idempotency-Key`` header; a double-tap / retry that
carries the same key must NOT create a second contract. Two layers:
  * the endpoint pre-checks (tenant_id, idempotency_key) under the existing
    per-tenant advisory lock and returns the existing contract on replay;
  * ``uq_installments_idem`` is the durable DB backstop.

DB-backed tests self-skip without TEST_DATABASE_URL (the standard ``migrated_db``
gate); the source-wiring guard always runs.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

# asyncio_mode = "auto" (pyproject) drives the async tests; no module mark needed.


def test_f12_model_and_endpoint_are_wired() -> None:
    """Source guards: the model declares the column + partial unique index and the
    endpoint reads the header and pre-checks before creating."""
    from pathlib import Path

    model_src = Path("app/models/installment.py").read_text()
    assert 'idempotency_key' in model_src
    assert 'uq_installments_idem' in model_src
    assert "idempotency_key IS NOT NULL" in model_src

    ep_src = Path("app/api/tenant/installments.py").read_text()
    assert 'alias="Idempotency-Key"' in ep_src
    # The pre-check selects an existing contract by (tenant_id, idempotency_key).
    assert 'Installment.idempotency_key == idempotency_key' in ep_src


async def test_f12_unique_index_enforces_idempotency(migrated_db) -> None:
    """The DB rejects a second contract with the same (tenant_id, idempotency_key),
    and allows many NULL keys (the index is partial)."""
    from app.models import Customer, Installment, Tenant, User
    from app.models.enums import CustomerStatus, InstallmentStatus, TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    def _ins(tid, cid, key):
        return Installment(currency="UZS",
            tenant_id=tid, customer_id=cid, status=InstallmentStatus.active,
            total_amount=Decimal("1000"), down_payment=Decimal("0"),
            principal=Decimal("1000"), monthly_amount=Decimal("100"),
            months=10, paid_count=0, paid_amount=Decimal("0"),
            start_date=date.today(), idempotency_key=key,
        )

    async with Session() as s:
        t = Tenant(slug="f12-t", name="T", plan_code="trial", status=TenantStatus.active)
        u = User(telegram_id=120120)
        s.add_all([t, u]); await s.flush()
        c = Customer(tenant_id=t.id, full_name="Idem Buyer", phone="+998900012012",
                     telegram_id=120121, status=CustomerStatus.active)
        s.add(c); await s.flush()
        tid, cid = t.id, c.id
        s.add(_ins(tid, cid, "dup-key")); await s.commit()

    # Same (tenant, key) → rejected.
    async with Session() as s:
        s.add(_ins(tid, cid, "dup-key"))
        with pytest.raises(IntegrityError):
            await s.commit()

    # NULL keys never collide (partial index excludes them).
    async with Session() as s:
        s.add(_ins(tid, cid, None)); s.add(_ins(tid, cid, None))
        await s.commit()  # no raise


async def test_f12_create_installment_idempotent_replay(migrated_db, monkeypatch) -> None:
    """Two POSTs with the same Idempotency-Key return the SAME contract and create
    exactly one row; a third POST without the key creates a distinct contract."""
    import httpx
    from httpx import ASGITransport

    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import Customer, Installment, Tenant, TenantUser, User
    from app.models.enums import CustomerStatus, TenantRole, TenantStatus
    from app.services import customer_outreach

    notified_installments: list[str] = []

    async def _record_notification(_db, *, installment, **_kwargs) -> bool:
        notified_installments.append(str(installment.id))
        return True

    # Idempotency is exercised entirely in-process. Never let this integration
    # test contact Telegram or leave the shared aiohttp session open.
    monkeypatch.setattr(customer_outreach, "notify_new_contract", _record_notification)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        # Generous explicit contract limit so the plan gate never blocks the test.
        t = Tenant(slug="f12-http", name="T", plan_code="trial",
                   status=TenantStatus.active,
                   plan_overrides={"limits": {"contract": 100}})
        u = User(telegram_id=120200)
        s.add_all([t, u]); await s.flush()
        c = Customer(tenant_id=t.id, full_name="Replay Buyer", phone="+998900012020",
                     telegram_id=120201, status=CustomerStatus.active)
        s.add(c); await s.flush()
        s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner))
        tid, uid, cid = t.id, u.id, c.id
        await s.commit()

    body = {
        "customer_id": str(cid), "months": 6, "down_payment": 0,
        "items": [{"product_name_snapshot": "Phone", "qty": 1, "unit_price": 1200000}],
    }
    auth = {"Authorization": f"Bearer {issue_token(user_id=str(uid))}", "X-Tenant-Id": str(tid)}

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cl:
            r1 = await cl.post("/api/tenant/installments", headers={**auth, "Idempotency-Key": "k-abc"}, json=body)
            r2 = await cl.post("/api/tenant/installments", headers={**auth, "Idempotency-Key": "k-abc"}, json=body)
            r3 = await cl.post("/api/tenant/installments", headers=auth, json=body)  # no key → new
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r1.status_code == 201, r1.text[:200]
    assert r2.status_code in (200, 201), r2.text[:200]
    # Idempotent replay returns the SAME contract.
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["contract_number"] == r2.json()["contract_number"]
    assert r3.status_code == 201 and r3.json()["id"] != r1.json()["id"]
    assert notified_installments == [r1.json()["id"], r3.json()["id"]]

    async with Session() as s:
        n_keyed = (await s.execute(
            select(func.count()).select_from(Installment).where(
                Installment.tenant_id == tid, Installment.idempotency_key == "k-abc")
        )).scalar_one()
    assert n_keyed == 1, f"idempotency key must map to exactly one contract, got {n_keyed}"
