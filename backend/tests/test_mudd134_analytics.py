"""MUDD-134 — owner dashboard analytics API.

``app.services.analytics.owner_summary`` / ``category_sales`` are exercised
directly against the REAL migrated schema (``migrated_db`` — real RLS-bearing
schema, connected as the superuser so the test focuses on the aggregation
logic rather than RLS), which is also where the platform's seeded catalog
categories/brands/models (r96catidschema / r97catmodels) actually exist.

Tenant isolation is proven over real HTTP via ``isolation_ctx``, in the style
of ``test_tenant_isolation.py`` (#79) — a model-only schema, so the guarantee
under test is the application-level ``WHERE tenant_id = ...`` scoping these
endpoints add, not RLS (that is covered elsewhere, e.g. test_rls_isolation.py).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.router import api_router


def test_analytics_endpoints_registered() -> None:
    paths = {r.path for r in api_router.routes}  # type: ignore[attr-defined]
    assert "/tenant/analytics/summary" in paths
    assert "/tenant/analytics/sales-by-category" in paths


# ── seeding helpers (real migrated schema, superuser session) ───────────────

async def _seed_tenant(engine, *, slug: str) -> tuple[UUID, UUID]:
    from app.models import Customer, Tenant

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug=slug, name=f"Tenant {slug}")
        s.add(t)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="Test Customer", phone="+998901112233")
        s.add(c)
        await s.commit()
        return t.id, c.id


async def _add_installment(
    engine, tenant_id: UUID, customer_id: UUID, *,
    currency: str = "UZS", status: str = "active",
    start: date = date(2026, 1, 15), next_due: date | None = None,
    items: tuple[dict, ...] = (),
) -> UUID:
    from app.models import Installment, InstallmentItem
    from app.models.enums import InstallmentStatus

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        total = sum((Decimal(str(it["unit_price"])) * it.get("qty", 1) for it in items), Decimal("0")) \
            or Decimal("1200000")
        inst = Installment(
            currency=currency, tenant_id=tenant_id, customer_id=customer_id,
            total_amount=total, principal=total, down_payment=Decimal("0"),
            months=6, monthly_amount=(total / 6).quantize(Decimal("1")),
            paid_amount=Decimal("0"), start_date=start, next_due_date=next_due,
            status=InstallmentStatus(status),
        )
        s.add(inst)
        await s.flush()
        for it in items:
            qty = it.get("qty", 1)
            unit_price = Decimal(str(it["unit_price"]))
            s.add(InstallmentItem(
                installment_id=inst.id,
                product_name_snapshot=it.get("name", "Item"),
                catalog_model_id=it.get("catalog_model_id"),
                category=it.get("category"),
                qty=qty, unit_price=unit_price, line_total=unit_price * qty,
            ))
        await s.commit()
        return inst.id


async def _add_payment(
    engine, tenant_id: UUID, installment_id: UUID, customer_id: UUID, *,
    currency: str = "UZS", amount: Decimal = Decimal("200000"),
    approved_at: datetime, status: str = "approved",
) -> UUID:
    from app.models import Payment
    from app.models.enums import PaymentMethod, PaymentStatus

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        pay = Payment(
            tenant_id=tenant_id, installment_id=installment_id, customer_id=customer_id,
            currency=currency, amount=amount, method=PaymentMethod.cash,
            status=PaymentStatus(status), paid_at=approved_at, approved_at=approved_at,
        )
        s.add(pay)
        await s.commit()
        return pay.id


async def _catalog_model(engine, *, category: str, brand: str, model: str) -> UUID:
    """A real seeded catalog_model id for (category, brand, model) slugs —
    never hardcoded, since the seed migration mints its ids randomly."""
    from app.models import CatalogBrand, CatalogCategory, CatalogModel

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        row = (await s.execute(
            select(CatalogModel.id)
            .join(CatalogBrand, CatalogBrand.id == CatalogModel.brand_id)
            .join(CatalogCategory, CatalogCategory.id == CatalogModel.category_id)
            .where(CatalogCategory.slug == category, CatalogBrand.slug == brand,
                   CatalogModel.slug == model)
        )).scalar_one()
        return row


# ── owner_summary ─────────────────────────────────────────────────────────

@pytest.mark.integration
async def test_summary_empty_tenant_is_well_formed_zeros(migrated_db) -> None:
    """A brand-new tenant with zero contracts gets real zeros, not nulls/500."""
    from app.services.analytics import owner_summary

    tid, _cid = await _seed_tenant(migrated_db, slug="an-empty")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        out = await owner_summary(
            s, tid, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
        )
    assert out["revenue"] == 0
    assert out["revenue_by_currency"] == {}
    assert out["debt"] == 0
    assert out["debt_by_currency"] == {}
    assert out["overdue_count"] == 0
    assert out["upcoming_count"] == 0
    assert out["customers_count"] == 1  # the seeded customer, zero contracts
    assert out["contracts_by_status"] == {}
    assert out["inventory"]["active_products_count"] == 0
    assert out["inventory"]["value"] == 0
    for ch in out["notifications"]:
        assert ch["sent"] == ch["failed"] == ch["pending"] == 0


@pytest.mark.integration
async def test_summary_never_sums_across_currencies(migrated_db) -> None:
    """A UZS contract and a USD contract must never be added into one number."""
    from app.services.analytics import owner_summary

    tid, cid = await _seed_tenant(migrated_db, slug="an-mixed")
    await _add_installment(
        migrated_db, tid, cid, currency="UZS", status="active",
        items=({"unit_price": "6000000", "qty": 1},),
    )
    await _add_installment(
        migrated_db, tid, cid, currency="USD", status="active",
        items=({"unit_price": "500", "qty": 1},),
    )
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        out = await owner_summary(
            s, tid, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
        )
    assert out["debt_by_currency"]["UZS"] == Decimal("6000000")
    assert out["debt_by_currency"]["USD"] == Decimal("500")
    # The primary-currency scalar is UZS's share alone — never 6000500-shaped.
    assert out["primary_currency"] == "UZS"
    assert out["debt"] == Decimal("6000000")


@pytest.mark.integration
async def test_summary_overdue_and_upcoming_are_disjoint(migrated_db) -> None:
    from app.services.analytics import owner_summary

    tid, cid = await _seed_tenant(migrated_db, slug="an-due")
    today = date(2026, 6, 15)
    # Already overdue.
    await _add_installment(
        migrated_db, tid, cid, currency="UZS", status="overdue",
        next_due=date(2026, 6, 1), items=({"unit_price": "1000000"},),
    )
    # Due later in the window — must show up as upcoming, not overdue.
    await _add_installment(
        migrated_db, tid, cid, currency="UZS", status="active",
        next_due=date(2026, 6, 20), items=({"unit_price": "2000000"},),
    )
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        out = await owner_summary(
            s, tid, date_from=date(2026, 6, 1), date_to=date(2026, 6, 30), today=today,
        )
    assert out["overdue_count"] == 1
    assert out["upcoming_count"] == 1
    assert out["overdue_by_currency"]["UZS"] == Decimal("1000000")


@pytest.mark.integration
async def test_summary_inventory_counts_and_values_active_products_only(migrated_db) -> None:
    from app.models import Product
    from app.services.analytics import owner_summary

    tid, _cid = await _seed_tenant(migrated_db, slug="an-inv")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        s.add_all([
            Product(tenant_id=tid, name_uz="Active w/ cost", base_price=Decimal("100"),
                    cost_price=Decimal("60"), stock=5, is_active=True),
            Product(tenant_id=tid, name_uz="Active no cost", base_price=Decimal("50"),
                    stock=3, is_active=True),
            Product(tenant_id=tid, name_uz="Inactive", base_price=Decimal("999"),
                    cost_price=Decimal("1"), stock=100, is_active=False),
        ])
        await s.commit()
        out = await owner_summary(
            s, tid, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
        )
    inv = out["inventory"]
    assert inv["active_products_count"] == 2          # inactive product excluded
    assert inv["units_in_stock"] == 8                 # 5 + 3, not 108
    assert inv["value"] == Decimal("300")             # 60*5 only — no-cost row contributes 0
    assert inv["products_missing_cost"] == 1


# ── category_sales ────────────────────────────────────────────────────────

@pytest.mark.integration
async def test_category_sales_covers_every_catalog_category_zero_filled(migrated_db) -> None:
    """Every category the platform knows about appears, even with 0 sales —
    read from the DB, not a hardcoded list."""
    from sqlalchemy import func

    from app.models import CatalogCategory
    from app.services.analytics import category_sales

    tid, _cid = await _seed_tenant(migrated_db, slug="an-catall")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        out = await category_sales(s, tid)
        n_categories = (await s.execute(
            select(func.count()).select_from(CatalogCategory)
        )).scalar_one()
    assert len(out["categories"]) == n_categories
    assert all(c["qty"] == 0 for c in out["categories"])


@pytest.mark.integration
async def test_category_sales_drilldown_and_legacy_bucket(migrated_db) -> None:
    """Catalog-backed sales roll up to (category, brand, model); a sale whose
    item has no catalog_model_id still appears, under brand/model = None —
    never silently dropped."""
    from app.services.analytics import category_sales

    tid, cid = await _seed_tenant(migrated_db, slug="an-drill")
    iphone_id = await _catalog_model(migrated_db, category="smartfonlar", brand="apple", model="iphone-15")

    await _add_installment(
        migrated_db, tid, cid, currency="UZS", status="active",
        items=({"unit_price": "9000000", "qty": 1, "category": "smartfonlar",
                "catalog_model_id": iphone_id, "name": "iPhone 15"},),
    )
    # Legacy line: resolved a category snapshot but never picked a catalog model.
    await _add_installment(
        migrated_db, tid, cid, currency="UZS", status="active",
        items=({"unit_price": "3000000", "qty": 1, "category": "smartfonlar",
                "catalog_model_id": None, "name": "No-name phone"},),
    )

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        out = await category_sales(s, tid, category="smartfonlar")

    by_key = {(r["brand_slug"], r["model_slug"]): r for r in out["rows"]}
    assert ("apple", "iphone-15") in by_key
    assert by_key[("apple", "iphone-15")]["revenue"] == Decimal("9000000")
    # Legacy bucket present, not dropped.
    assert (None, None) in by_key
    assert by_key[(None, None)]["revenue"] == Decimal("3000000")
    smartfonlar_total = next(c for c in out["categories"] if c["category_slug"] == "smartfonlar")
    assert smartfonlar_total["qty"] == 2
    assert smartfonlar_total["revenue"] == Decimal("12000000")


@pytest.mark.integration
async def test_category_sales_brand_filter_narrows_rows(migrated_db) -> None:
    from app.services.analytics import category_sales

    tid, cid = await _seed_tenant(migrated_db, slug="an-brandf")
    iphone_id = await _catalog_model(migrated_db, category="smartfonlar", brand="apple", model="iphone-15")
    await _add_installment(
        migrated_db, tid, cid, currency="UZS", status="active",
        items=({"unit_price": "9000000", "category": "smartfonlar",
                "catalog_model_id": iphone_id, "name": "iPhone 15"},),
    )

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        matching = await category_sales(s, tid, category="smartfonlar", brand="apple")
        nonmatching = await category_sales(s, tid, category="smartfonlar", brand="samsung")

    assert len(matching["rows"]) == 1
    assert matching["rows"][0]["brand_slug"] == "apple"
    assert nonmatching["rows"] == []
    assert nonmatching["total_qty"] == 0


@pytest.mark.integration
async def test_category_sales_date_range_filter(migrated_db) -> None:
    from app.services.analytics import category_sales

    tid, cid = await _seed_tenant(migrated_db, slug="an-daterange")
    await _add_installment(
        migrated_db, tid, cid, currency="UZS", status="active", start=date(2026, 1, 5),
        items=({"unit_price": "1000000", "category": "smartfonlar"},),
    )
    await _add_installment(
        migrated_db, tid, cid, currency="UZS", status="active", start=date(2026, 3, 5),
        items=({"unit_price": "2000000", "category": "smartfonlar"},),
    )

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        jan_only = await category_sales(
            s, tid, date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
        )
        both = await category_sales(
            s, tid, date_from=date(2026, 1, 1), date_to=date(2026, 3, 31),
        )
    jan_cat = next(c for c in jan_only["categories"] if c["category_slug"] == "smartfonlar")
    both_cat = next(c for c in both["categories"] if c["category_slug"] == "smartfonlar")
    assert jan_cat["revenue"] == Decimal("1000000")
    assert both_cat["revenue"] == Decimal("3000000")


@pytest.mark.integration
async def test_category_sales_cancelled_contracts_excluded(migrated_db) -> None:
    from app.services.analytics import category_sales

    tid, cid = await _seed_tenant(migrated_db, slug="an-cxl")
    await _add_installment(
        migrated_db, tid, cid, currency="UZS", status="cancelled",
        items=({"unit_price": "5000000", "category": "smartfonlar"},),
    )
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        out = await category_sales(s, tid)
    smartfonlar = next(c for c in out["categories"] if c["category_slug"] == "smartfonlar")
    assert smartfonlar["qty"] == 0
    assert smartfonlar["revenue"] == 0


# ── cash-flow trend: tenant business day, not UTC (MUDD-140) ────────────────

@pytest.mark.integration
async def test_cashflow_buckets_by_tenant_business_day_not_utc(migrated_db) -> None:
    """A payment approved at 01:00 Asia/Tashkent (still 20:00 the PREVIOUS day
    in UTC) must land on ITS local calendar day — the bug this lane fixes was
    `date_trunc` running in the DB session's zone (UTC), which put every
    00:00-05:00 Tashkent payment on the wrong bar."""
    from app.services.reports import cashflow

    tid, cid = await _seed_tenant(migrated_db, slug="tz-boundary")
    inst_id = await _add_installment(migrated_db, tid, cid, start=date(2026, 1, 1))
    # 2026-01-02 01:00 Asia/Tashkent == 2026-01-01 20:00 UTC. A naive UTC
    # `date_trunc` buckets this under Jan 1; the tenant's own calendar says Jan 2.
    approved_at = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)
    await _add_payment(migrated_db, tid, inst_id, cid, amount=Decimal("150000"),
                       approved_at=approved_at)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        jan2_only = await cashflow(s, tid, date_from=date(2026, 1, 2), date_to=date(2026, 1, 2))
        jan1_only = await cashflow(s, tid, date_from=date(2026, 1, 1), date_to=date(2026, 1, 1))

    assert jan2_only["total_by_currency"] == {"UZS": Decimal("150000")}
    assert jan2_only["series"][0]["period"] == "2026-01-02"
    assert jan1_only["total_by_currency"] == {}


@pytest.mark.integration
async def test_cashflow_refund_removes_income_from_original_bucket(migrated_db) -> None:
    """Refund semantics are RULED (not a decision left open): `refunded` is a
    terminal state on the original row, so filtering `status == approved`
    already removes it from the day it was received — pinned here so that
    stays true rather than incidental. A refund today can therefore change a
    past day's reported figure; that consequence is intentional (see MUDD-140
    routing comment on this issue)."""
    from sqlalchemy import select as sa_select

    from app.models import Payment
    from app.models.enums import PaymentStatus
    from app.services.reports import cashflow

    tid, cid = await _seed_tenant(migrated_db, slug="refund-pin")
    inst_id = await _add_installment(migrated_db, tid, cid, start=date(2026, 2, 1))
    approved_at = datetime(2026, 2, 5, 6, 0, tzinfo=UTC)  # midday Tashkent, no boundary ambiguity
    pay_id = await _add_payment(migrated_db, tid, inst_id, cid, amount=Decimal("300000"),
                                approved_at=approved_at)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        before = await cashflow(s, tid, date_from=date(2026, 2, 5), date_to=date(2026, 2, 5))
    assert before["total_by_currency"] == {"UZS": Decimal("300000")}

    async with Session() as s:
        await s.execute(
            Payment.__table__.update().where(Payment.id == pay_id)
            .values(status=PaymentStatus.refunded)
        )
        await s.commit()

    async with Session() as s:
        after = await cashflow(s, tid, date_from=date(2026, 2, 5), date_to=date(2026, 2, 5))
        still_there = (await s.execute(
            sa_select(Payment.status).where(Payment.id == pay_id)
        )).scalar_one()
    assert after["total_by_currency"] == {}
    assert still_there == PaymentStatus.refunded  # the row persists; only the bucket empties


@pytest.mark.integration
async def test_report_cashflow_default_window_anchors_on_tenant_day_not_utc(
    migrated_db, monkeypatch,
) -> None:
    """Regression for the third MUDD-140 clock: `report_cashflow`'s own
    `to = date_to or date.today()` read the CONTAINER's (UTC) date, not the
    tenant's, even though `svc.cashflow` itself now buckets correctly. At
    20:00 UTC it is already 01:00 the NEXT day in Asia/Tashkent — a no-args
    request made at that instant must still cover today's (tenant-local)
    payment, not silently clip it off the end of the default 30-day window.
    Fixed by anchoring on `_tenant_today(tenant)` — the same helper the
    collections worklist already uses (`app/api/tenant/_shared.py`), so the
    dashboard and the worklist cannot disagree about which day it is."""
    from app.api.reports import report_cashflow
    from app.api.tenant import _shared as tenant_shared
    from app.models import Tenant, TenantUser
    from app.models.enums import TenantRole

    tid, cid = await _seed_tenant(migrated_db, slug="cashflow-default-window")
    inst_id = await _add_installment(migrated_db, tid, cid, start=date(2026, 3, 1))
    # 2026-03-02 01:00 Asia/Tashkent == 2026-03-01 20:00 UTC: already "today"
    # for the tenant, while the container's UTC clock still reads March 1.
    approved_at = datetime(2026, 3, 1, 20, 0, tzinfo=UTC)
    await _add_payment(migrated_db, tid, inst_id, cid, amount=Decimal("90000"),
                       approved_at=approved_at)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 3, 1, 20, 30, tzinfo=UTC)
            return fixed.astimezone(tz) if tz else fixed

    monkeypatch.setattr(tenant_shared, "datetime", _FixedDatetime)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.id == tid))).scalar_one()
        membership = TenantUser(tenant_id=tid, user_id=UUID(int=0), role=TenantRole.owner)
        data = await report_cashflow(
            ctx=(tenant, membership), db=s, date_from=None, date_to=None,
        )

    assert data["to"] == "2026-03-02"
    assert data["total_by_currency"] == {"UZS": Decimal("90000")}


# ── tenant isolation over real HTTP (#79-style) ──────────────────────────────

async def test_analytics_scoped_to_own_tenant(isolation_ctx) -> None:
    """A second tenant's contracts/products must never influence A's analytics
    — the model-only schema here exercises the application-level `tenant_id`
    scoping these endpoints add (RLS itself is proven in test_rls_isolation.py)."""
    from app.db import SessionLocal
    from app.models import Installment, InstallmentItem, Product
    from app.models.enums import InstallmentStatus

    client, ctx = isolation_ctx
    tenant_a, tenant_b = UUID(ctx["tenant_a"]), UUID(ctx["tenant_b"])
    customer_a, customer_b = UUID(ctx["customer_a"]), UUID(ctx["customer_b"])

    async with SessionLocal() as s:
        inst_a = Installment(
            currency="UZS", tenant_id=tenant_a, customer_id=customer_a,
            total_amount=Decimal("1000000"), principal=Decimal("1000000"),
            down_payment=Decimal("0"), months=6, monthly_amount=Decimal("166667"),
            paid_amount=Decimal("0"), start_date=date(2026, 1, 10),
            status=InstallmentStatus.active,
        )
        inst_b = Installment(
            currency="UZS", tenant_id=tenant_b, customer_id=customer_b,
            total_amount=Decimal("9000000"), principal=Decimal("9000000"),
            down_payment=Decimal("0"), months=6, monthly_amount=Decimal("1500000"),
            paid_amount=Decimal("0"), start_date=date(2026, 1, 10),
            status=InstallmentStatus.active,
        )
        s.add_all([inst_a, inst_b])
        await s.flush()
        s.add_all([
            InstallmentItem(installment_id=inst_a.id, product_name_snapshot="A item",
                             category="smartfonlar", qty=1, unit_price=Decimal("1000000"),
                             line_total=Decimal("1000000")),
            InstallmentItem(installment_id=inst_b.id, product_name_snapshot="B item",
                             category="smartfonlar", qty=1, unit_price=Decimal("9000000"),
                             line_total=Decimal("9000000")),
        ])
        s.add_all([
            Product(tenant_id=tenant_a, name_uz="A product", base_price=Decimal("1"),
                    stock=1, is_active=True),
            Product(tenant_id=tenant_b, name_uz="B product", base_price=Decimal("1"),
                    stock=999, is_active=True),
        ])
        await s.commit()

    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    r_summary = await client.get(
        "/api/tenant/analytics/summary?from=2026-01-01&to=2026-01-31", headers=auth,
    )
    assert r_summary.status_code == 200, r_summary.text[:300]
    body = r_summary.json()
    assert body["debt_by_currency"]["UZS"] == 1000000  # A's own debt only
    assert body["inventory"]["active_products_count"] == 1

    r_cat = await client.get(
        "/api/tenant/analytics/sales-by-category?category=smartfonlar", headers=auth,
    )
    assert r_cat.status_code == 200, r_cat.text[:300]
    cat_body = r_cat.json()
    assert cat_body["total_revenue"] == 1000000  # never B's 9,000,000
    assert sum(row["qty"] for row in cat_body["rows"]) == 1  # only A's one line
