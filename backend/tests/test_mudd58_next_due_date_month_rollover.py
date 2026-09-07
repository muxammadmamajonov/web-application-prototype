"""MUDD-58 (H12) — the first `next_due_date` must roll a full calendar month,
not clamp the day to 28.

Both contract-creation and contract-replace hand-rolled
``date(y, m+1, min(day, 28))`` instead of calling the existing
``services.installments.add_months`` helper (which clamps against the target
month's REAL length via ``calendar.monthrange``). A contract started on the
29th/30th/31st therefore got its first due date shrunk onto the 28th — up to
3 days early — silently moving the customer's due date and every late-fee/
dunning calculation derived from it.

DB-backed: self-skips without TEST_DATABASE_URL (the standard integration
gate); the source-wiring guard always runs.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker


def test_mudd58_no_hand_rolled_28_day_clamp_remains() -> None:
    """Source guard: the buggy inline formula must be gone from the endpoint
    file, and both call sites must route through `add_months`."""
    from pathlib import Path

    src = Path("app/api/tenant/installments.py").read_text()
    assert "min(start.day, 28)" not in src
    assert src.count("add_months(start, 1)") == 2


@pytest.mark.parametrize(
    "start, expected_next_due",
    [
        (date(2026, 1, 31), date(2026, 2, 28)),   # Jan 31 -> Feb 28 (non-leap)
        (date(2024, 1, 29), date(2024, 2, 29)),   # Jan 29 -> Feb 29 (leap year)
        (date(2026, 3, 31), date(2026, 4, 30)),   # Mar 31 -> Apr 30, NOT Apr 28
        (date(2026, 4, 30), date(2026, 5, 30)),   # Apr 30 -> May 30 (both 30/31-day)
        (date(2026, 12, 31), date(2027, 1, 31)),  # Dec 31 -> Jan 31, year rollover
    ],
)
async def test_mudd58_create_installment_first_due_date(
    migrated_db, monkeypatch, start: date, expected_next_due: date,
) -> None:
    import httpx
    from httpx import ASGITransport

    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import Customer, Tenant, TenantUser, User
    from app.models.enums import CustomerStatus, TenantRole, TenantStatus
    from app.services import customer_outreach

    async def _no_notify(_db, *, installment, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(customer_outreach, "notify_new_contract", _no_notify)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug=f"mudd58-{start.isoformat()}", name="T", plan_code="trial",
                   status=TenantStatus.active,
                   plan_overrides={"limits": {"contract": 100}})
        u = User(telegram_id=int(f"58{start.month:02d}{start.day:02d}"))
        s.add_all([t, u]); await s.flush()
        c = Customer(tenant_id=t.id, full_name="Rollover Buyer",
                     phone=f"+99890{start.month:02d}{start.day:02d}001",
                     telegram_id=int(f"58{start.month:02d}{start.day:02d}1"),
                     status=CustomerStatus.active)
        s.add(c); await s.flush()
        s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner))
        tid, uid, cid = t.id, u.id, c.id
        await s.commit()

    body = {
        "customer_id": str(cid), "months": 6, "down_payment": 0,
        "start_date": start.isoformat(),
        "items": [{"product_name_snapshot": "Phone", "qty": 1, "unit_price": 1200000}],
    }
    auth = {"Authorization": f"Bearer {issue_token(user_id=str(uid))}", "X-Tenant-Id": str(tid)}

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cl:
            r = await cl.post("/api/tenant/installments", headers=auth, json=body)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 201, r.text[:300]
    assert r.json()["next_due_date"] == expected_next_due.isoformat()


async def test_mudd58_replace_installment_first_due_date(migrated_db, monkeypatch) -> None:
    """The contract-replace path (PUT) hits the second call site — 31 Mar -> 30 Apr."""
    import httpx
    from httpx import ASGITransport

    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import Customer, Installment, Tenant, TenantUser, User
    from app.models.enums import CustomerStatus, InstallmentStatus, TenantRole, TenantStatus
    from app.services import customer_outreach

    async def _no_notify(_db, *, installment, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(customer_outreach, "notify_new_contract", _no_notify)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="mudd58-replace", name="T", plan_code="trial",
                   status=TenantStatus.active,
                   plan_overrides={"limits": {"contract": 100}})
        u = User(telegram_id=58990001)
        s.add_all([t, u]); await s.flush()
        c = Customer(tenant_id=t.id, full_name="Replace Buyer", phone="+998900058991",
                     telegram_id=58990002, status=CustomerStatus.active)
        s.add(c); await s.flush()
        s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner))
        ins = Installment(
            tenant_id=t.id, customer_id=c.id, currency="UZS",
            status=InstallmentStatus.active,
            total_amount=Decimal("1200000"), down_payment=Decimal("0"),
            principal=Decimal("1200000"), monthly_amount=Decimal("200000"),
            months=6, start_date=date(2026, 1, 1),
            paid_count=0, paid_amount=Decimal("0"),
        )
        s.add(ins); await s.flush()
        tid, uid, cid, iid = t.id, u.id, c.id, ins.id
        await s.commit()

    body = {
        "customer_id": str(cid), "months": 6, "down_payment": 0,
        "start_date": date(2026, 3, 31).isoformat(),
        "items": [{"product_name_snapshot": "Phone", "qty": 1, "unit_price": 1200000}],
    }
    auth = {"Authorization": f"Bearer {issue_token(user_id=str(uid))}", "X-Tenant-Id": str(tid)}

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as cl:
            r = await cl.put(f"/api/tenant/installments/{iid}", headers=auth, json=body)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 200, r.text[:300]
    assert r.json()["next_due_date"] == date(2026, 4, 30).isoformat()
