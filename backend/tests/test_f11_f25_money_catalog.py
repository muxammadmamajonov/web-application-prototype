"""F-11 + F-25 remediation.

F-11 — ``tenant_plan_change`` must do every money comparison/debit in Decimal via
``round_amount``; the old ``int(target.price_monthly)`` / ``int(tenant.balance)``
truncated Numeric(14,2) so a 1000.50 plan charged 1000.00 (revenue leak) and a
999.99 balance read as 999 (a spurious 402 with a misleading shortfall).

F-25 — a tenant-``proposed`` CatalogModel (no PII/financials, ``tenant_id`` set)
must NOT be readable by another tenant who guesses its UUID: both ``get_model``
and ``_hydrate_from_catalog`` are scoped to
``or_(tenant_id IS NULL, tenant_id == <caller>)``. Global (NULL-tenant) rows stay
readable by everyone. ``propose_model`` (a tenant-scoped write) is gated to
owner/manager — a sales member is rejected.

The pure-logic + source-guard tests run everywhere (no DB). The behavioural
HTTP/DB tests self-skip without ``TEST_DATABASE_URL`` (shared local PG) via the
``migrated_db`` fixture — same convention as the other Fxx suites.
"""
from __future__ import annotations

import inspect
from decimal import Decimal

import httpx
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.money_utils import round_amount

# ── F-11: pure money logic (no DB) ───────────────────────────────────────────

def test_f11_int_truncation_would_leak_revenue_and_misreport_balance() -> None:
    """The exact bug the fix removes: int() on Numeric(14,2) loses the kopeks.

    A 1000.50 plan truncates to 1000 (the shop is under-charged) and a 999.99
    balance truncates to 999 (a 999.50 plan looks unaffordable). round_amount
    preserves both — this asserts the truncation is gone, not merely that the
    happy path works.
    """
    price = Decimal("1000.50")
    balance = Decimal("999.99")

    # The old behaviour (documented here so a regression to int() is obvious).
    assert int(price) == 1000          # ← revenue leak: 0.50 lost per charge
    assert int(balance) == 999          # ← spurious shortfall: 0.99 lost

    # The fix keeps full precision.
    assert round_amount(price) == Decimal("1000.50")
    assert round_amount(balance) == Decimal("999.99")
    assert round_amount(price) != int(price)
    assert round_amount(balance) != int(balance)


def test_f11_debit_is_exact_decimal() -> None:
    """Debiting a 1000.50 plan from a 5000.00 balance leaves exactly 3999.50 —
    never 4000.00 (which int(price) would produce)."""
    balance = round_amount(Decimal("5000.00"))
    price = round_amount(Decimal("1000.50"))
    after = round_amount(balance) - price
    assert after == Decimal("3999.50")
    # int-truncated price would wrongly leave 4000.00.
    assert balance - Decimal(int(price)) == Decimal("4000.00")


def test_f11_balance_999_99_affords_999_50_plan_no_false_402() -> None:
    """Affordability is a Decimal comparison: 999.99 >= 999.50, so NO 402.

    The fix compares full Decimals (affordable). The old int() path truncated
    the balance to 999 — and 999 < 999.50 (price compared as int(999)) made the
    plan look unaffordable, i.e. a spurious 402.
    """
    balance = round_amount(Decimal("999.99"))
    price = round_amount(Decimal("999.50"))
    assert balance >= price, "999.99 must afford a 999.50 plan"

    # Reproduce the old false-402: int(balance) vs the Decimal price.
    assert int(balance) < price, "truncated balance (999) wrongly fails the 999.50 check"
    assert int(Decimal("999.99")) == 999


# ── F-11 + F-25: source guards (no DB) ───────────────────────────────────────

def test_f11_plan_change_path_has_no_int_money_calls() -> None:
    """No int()/float() may wrap a money value anywhere in tenant_plan_change.

    Scans the function source: ``int(target.price...``, ``int(tenant.balance``,
    ``int(price)``, ``int(balance)`` and the float() equivalents must all be
    absent. (``str(...)`` / ``round_amount(...)`` are the only money conversions.)
    """
    from app.api.tenant import tenant_plan_change

    src = inspect.getsource(tenant_plan_change)
    forbidden = [
        "int(target.price",
        "int(tenant.balance",
        "int(price",
        "int(balance",
        "int(self.balance",
        "float(target.price",
        "float(tenant.balance",
        "float(price",
        "float(balance",
    ]
    hits = [f for f in forbidden if f in src]
    assert not hits, f"int()/float() money truncation remains in plan-change path: {hits}"
    # The fix must actually compute price/balance via round_amount.
    assert "round_amount(target.price_monthly" in src
    assert "round_amount(tenant.balance" in src


def test_f25_get_model_is_tenant_scoped() -> None:
    """get_model must filter by or_(tenant_id IS NULL, tenant_id == caller)."""
    from app.api import catalog

    src = inspect.getsource(catalog.get_model)
    assert "_optional_tenant_id" in src, "get_model must resolve the caller's tenant"
    assert "CatalogModel.tenant_id.is_(None)" in src
    assert "CatalogModel.tenant_id == tenant_id" in src


def test_f25_hydrate_from_catalog_is_tenant_scoped() -> None:
    """_hydrate_from_catalog must take a tenant_id and scope its model read."""
    from app.api import tenant as tenant_api

    src = inspect.getsource(tenant_api._hydrate_from_catalog)
    assert "tenant_id" in inspect.signature(tenant_api._hydrate_from_catalog).parameters
    assert "CatalogModel.tenant_id.is_(None)" in src
    assert "CatalogModel.tenant_id == tenant_id" in src
    # …and the only caller passes the caller's tenant.
    create_src = inspect.getsource(tenant_api.create_product)
    assert "_hydrate_from_catalog(db, body.catalog_model_id, data, tenant_id=tenant.id)" in create_src


def test_f25_propose_model_has_role_gate() -> None:
    """propose_model is a tenant-scoped write — must call _require_owner_or_manager."""
    from app.api import catalog

    src = inspect.getsource(catalog.propose_model)
    assert "_require_owner_or_manager(membership)" in src


# ── F-25: behavioural (DB-backed, self-skips without TEST_DATABASE_URL) ───────

async def test_f25_cannot_read_other_tenants_proposed_model_by_uuid(migrated_db) -> None:
    """Tenant A asks GET /catalog/models/<B's proposed model id> → 404, while a
    global (NULL-tenant) model is readable; B can still read its own proposal."""
    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import (
        CatalogBrand,
        CatalogCategory,
        CatalogModel,
        Tenant,
        TenantUser,
        User,
    )
    from app.models.enums import TenantRole, TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        cat = CatalogCategory(slug="f25-cat", name_uz="C", name_ru="C")
        brand = CatalogBrand(slug="f25-brand", name="F25", category_id=None, category_ids=[])
        s.add_all([cat, brand])
        await s.flush()
        brand.category_id = cat.id
        brand.category_ids = [str(cat.id)]

        ta = Tenant(slug="f25-a", name="A", plan_code="trial", status=TenantStatus.active)
        tb = Tenant(slug="f25-b", name="B", plan_code="trial", status=TenantStatus.active)
        ua = User(telegram_id=250001)
        s.add_all([ta, tb, ua])
        await s.flush()
        s.add(TenantUser(tenant_id=ta.id, user_id=ua.id, role=TenantRole.owner))

        # B's private proposed model + a shared global model.
        b_model = CatalogModel(
            brand_id=brand.id, category_id=cat.id, slug="b-secret", name="B Secret",
            tenant_id=tb.id, status="proposed", is_active=True,
        )
        global_model = CatalogModel(
            brand_id=brand.id, category_id=cat.id, slug="g-public", name="Global",
            tenant_id=None, status="global", is_active=True,
        )
        s.add_all([b_model, global_model])
        await s.flush()
        a_tid, ua_id = ta.id, ua.id
        b_model_id, global_model_id = b_model.id, global_model.id
        await s.commit()

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    auth_a = {"Authorization": f"Bearer {issue_token(user_id=str(ua_id))}",
              "X-Tenant-Id": str(a_tid)}
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            # A cannot read B's proposed model by its UUID.
            r_cross = await c.get(f"/api/catalog/models/{b_model_id}", headers=auth_a)
            # The global model is still readable by A.
            r_global = await c.get(f"/api/catalog/models/{global_model_id}", headers=auth_a)
            # Anonymous (no auth) also cannot see B's proposed model.
            r_anon = await c.get(f"/api/catalog/models/{b_model_id}")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r_cross.status_code == 404, (
        f"tenant A must not read tenant B's proposed model — got {r_cross.status_code}: "
        f"{r_cross.text[:160]}"
    )
    assert r_anon.status_code == 404, "anonymous caller must not read a tenant-proposed model"
    assert r_global.status_code == 200, (
        f"a global (NULL-tenant) model must stay readable — got {r_global.status_code}"
    )


async def test_f25_create_product_ignores_other_tenants_proposed_model(migrated_db) -> None:
    """_hydrate_from_catalog must not pull brand/name from another tenant's
    proposed model: creating a product with B's catalog_model_id leaves the
    hydrated fields unset (the scoped lookup returns nothing)."""
    from app.api.tenant import _hydrate_from_catalog
    from app.models import (
        CatalogBrand,
        CatalogCategory,
        CatalogModel,
        Tenant,
    )
    from app.models.enums import TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        cat = CatalogCategory(slug="f25h-cat", name_uz="C", name_ru="C")
        brand = CatalogBrand(slug="f25h-brand", name="F25H", category_id=None, category_ids=[])
        s.add_all([cat, brand])
        await s.flush()
        brand.category_id = cat.id
        brand.category_ids = [str(cat.id)]
        ta = Tenant(slug="f25h-a", name="A", plan_code="trial", status=TenantStatus.active)
        tb = Tenant(slug="f25h-b", name="B", plan_code="trial", status=TenantStatus.active)
        s.add_all([ta, tb])
        await s.flush()
        b_model = CatalogModel(
            brand_id=brand.id, category_id=cat.id, slug="b-only", name="B Only",
            tenant_id=tb.id, status="proposed", is_active=True,
        )
        s.add(b_model)
        await s.flush()
        a_tid, b_model_id = ta.id, b_model.id
        await s.commit()

    async with Session() as s:
        # Tenant A tries to hydrate from B's proposed model → no-op.
        data: dict = {}
        await _hydrate_from_catalog(s, b_model_id, data, tenant_id=a_tid)
        assert data == {}, f"must not hydrate from another tenant's proposed model, got {data}"


async def test_f25_propose_model_rejects_sales_role(migrated_db) -> None:
    """A sales member calling POST /catalog/models/propose gets 403; an owner
    succeeds (201)."""
    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import (
        CatalogBrand,
        CatalogCategory,
        Tenant,
        TenantUser,
        User,
    )
    from app.models.enums import TenantRole, TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        cat = CatalogCategory(slug="f25p-cat", name_uz="C", name_ru="C")
        brand = CatalogBrand(slug="f25p-brand", name="F25P", category_id=None, category_ids=[])
        s.add_all([cat, brand])
        await s.flush()
        brand.category_id = cat.id
        brand.category_ids = [str(cat.id)]
        t = Tenant(slug="f25p-t", name="T", plan_code="trial", status=TenantStatus.active)
        u_sales = User(telegram_id=250777)
        u_owner = User(telegram_id=250778)
        s.add_all([t, u_sales, u_owner])
        await s.flush()
        s.add_all([
            TenantUser(tenant_id=t.id, user_id=u_sales.id, role=TenantRole.sales),
            TenantUser(tenant_id=t.id, user_id=u_owner.id, role=TenantRole.owner),
        ])
        tid, sales_id, owner_id = t.id, u_sales.id, u_owner.id
        brand_id, cat_id = brand.id, cat.id
        await s.commit()

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    body = {"brand_id": str(brand_id), "category_id": str(cat_id), "name": "Proposed X"}
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r_sales = await c.post(
                "/api/catalog/models/propose",
                headers={"Authorization": f"Bearer {issue_token(user_id=str(sales_id))}",
                         "X-Tenant-Id": str(tid)},
                json=body,
            )
            r_owner = await c.post(
                "/api/catalog/models/propose",
                headers={"Authorization": f"Bearer {issue_token(user_id=str(owner_id))}",
                         "X-Tenant-Id": str(tid)},
                json=body,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r_sales.status_code == 403, (
        f"sales must not propose a catalog model — got {r_sales.status_code}: {r_sales.text[:160]}"
    )
    assert r_owner.status_code == 201, (
        f"owner must be able to propose a model — got {r_owner.status_code}: {r_owner.text[:160]}"
    )


async def test_f11_plan_change_charges_exact_decimal_no_false_402(migrated_db) -> None:
    """End-to-end: a plan priced 1000.50 debits exactly 1000.50 (not 1000.00),
    and a balance of 999.99 affords a 999.50 plan with no false 402."""
    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import Plan, Tenant, TenantBalanceTxn, TenantUser, User
    from app.models.enums import TenantRole, TenantStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        # Plan priced with kopeks; tenant funded with 5000.00.
        plan = Plan(code="f11-pro", name_uz="Pro", name_ru="Pro",
                    price_monthly=Decimal("1000.50"))
        # A cheap plan to prove the 999.99 ≥ 999.50 affordability (no false 402).
        plan_cheap = Plan(code="f11-lite", name_uz="Lite", name_ru="Lite",
                          price_monthly=Decimal("999.50"))
        t = Tenant(slug="f11-t", name="T", plan_code="trial",
                   status=TenantStatus.active, balance=Decimal("5000.00"))
        t2 = Tenant(slug="f11-t2", name="T2", plan_code="trial",
                    status=TenantStatus.active, balance=Decimal("999.99"))
        u = User(telegram_id=110011)
        u2 = User(telegram_id=110012)
        s.add_all([plan, plan_cheap, t, t2, u, u2])
        await s.flush()
        s.add_all([
            TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner),
            TenantUser(tenant_id=t2.id, user_id=u2.id, role=TenantRole.owner),
        ])
        tid, uid = t.id, u.id
        tid2, uid2 = t2.id, u2.id
        await s.commit()

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r_charge = await c.post(
                "/api/tenant/plan-change",
                headers={"Authorization": f"Bearer {issue_token(user_id=str(uid))}",
                         "X-Tenant-Id": str(tid)},
                json={"plan_code": "f11-pro"},
            )
            r_afford = await c.post(
                "/api/tenant/plan-change",
                headers={"Authorization": f"Bearer {issue_token(user_id=str(uid2))}",
                         "X-Tenant-Id": str(tid2)},
                json={"plan_code": "f11-lite"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r_charge.status_code == 200, f"plan change failed: {r_charge.text[:200]}"
    # 999.99 balance must afford a 999.50 plan — the old int() path produced a 402.
    assert r_afford.status_code == 200, (
        f"999.99 balance must afford a 999.50 plan (no false 402) — "
        f"got {r_afford.status_code}: {r_afford.text[:200]}"
    )

    async with Session() as s:
        bal = (await s.execute(select(Tenant.balance).where(Tenant.id == tid))).scalar_one()
        assert round_amount(bal) == Decimal("3999.50"), (
            f"5000.00 − 1000.50 must be 3999.50 (no kopek truncation), got {bal}"
        )
        # The ledger row recorded the full Decimal charge, not a truncated int.
        charge_amt = (await s.execute(
            select(TenantBalanceTxn.amount).where(
                TenantBalanceTxn.tenant_id == tid,
                TenantBalanceTxn.kind == "plan_charge",
            )
        )).scalar_one()
        assert round_amount(charge_amt) == Decimal("-1000.50"), (
            f"plan_charge ledger must debit exactly -1000.50, got {charge_amt}"
        )

        bal2 = (await s.execute(select(Tenant.balance).where(Tenant.id == tid2))).scalar_one()
        assert round_amount(bal2) == Decimal("0.49"), (
            f"999.99 − 999.50 must be 0.49, got {bal2}"
        )
