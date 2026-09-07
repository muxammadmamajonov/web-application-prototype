"""Query-count *slope* over data cardinality for eight audited endpoints.

The question this module answers is narrow: **does the number of SQL statements
a request issues grow with the number of rows it returns or processes?**  That
is the definition of an N+1 defect, and it cannot be derived from source — the
handlers issue explicit queries rather than traversing relationships, so a
static read shows a *shape* (``await`` inside ``for``) without showing a *cost*.

Coverage
--------
Two groups, added in two passes and measured the same way:

* the four originally audited endpoints — ``GET /api/auth/me``,
  ``GET /api/platform/referrals``, ``GET /api/platform/overview`` and
  ``POST /api/tenant/installments`` — at cardinalities ``CARDINALITIES``
  (1, 10, 50);
* the four hot KEEP tenant reads added for REL-004 — ``GET /api/tenant/customers``,
  ``GET /api/tenant/installments``, ``GET /api/tenant/worklist`` and
  ``GET /api/tenant/payments`` — which additionally measure the **empty** page:
  ``KEEP_CARDINALITIES`` (0, 1, 10, 50), and ``WORKLIST_CARDINALITIES``
  (0, 1, 10, 20) because ``/tenant/worklist`` defaults to a 20-row page.

Method
------
Each endpoint is exercised over a real HTTP request against the live PostgreSQL
test tier, varying only the one thing that grows.  A SQLAlchemy
``before_cursor_execute`` listener is attached **immediately before** the request
and removed **immediately after**, so schema creation, seeding, token
construction and teardown are outside every measurement.  Statements are
bucketed into normalized families (verb + table) so a regression names the
relationship that caused it rather than only moving a total.

The invariant asserted is slope, not an absolute budget: a handler is allowed to
cost whatever it costs, but that cost must not be a function of row count.  The
assertion is **exact equality — no tolerance — across the non-empty volumes**,
because these query plans have no legitimate reason to vary with row count.

The empty page is held to a different, deliberately weaker rule (see
``assert_flat_and_empty_is_not_costlier``): it is measured, so a silently-empty
fixture cannot make "flat" pass by accident, but it is allowed to issue *fewer*
statements and only forbidden from issuing more.  Three of the REL-004 reads do
skip one name-lookup when there is nothing to decorate; that is a constant
short-circuit, not a slope, and folding it into the exact-equality assertion
would report it as an N+1 defect it is not.

Note that "flat" is about *round trips*, not rows: ``POST /tenant/installments``
still writes one ``installment_items`` and one ``stock_movements`` row per line,
but in one batched statement each.

Skips cleanly (never silently passes) without ``TEST_DATABASE_URL``; the first
test proves the tier is a real PostgreSQL connection rather than a stub.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event

#: Data volumes for the four originally audited endpoints. The REL-004 KEEP reads
#: use KEEP_CARDINALITIES / WORKLIST_CARDINALITIES, which also include the empty page.
CARDINALITIES = (1, 10, 50)

# ── statement classification ────────────────────────────────────────────────
#
# A "family" is a driver-noise-free label for a round trip: the verb plus the
# primary table it touches.  Bind parameters, literals and generated savepoint
# names are erased so two executions of the same query collapse to one family
# and a *count* per family is meaningful.

_SELECT_FROM = re.compile(r"^SELECT\b.*?\bFROM\s+([a-z_][a-z0-9_]*)", re.IGNORECASE | re.DOTALL)
_INSERT_INTO = re.compile(r"^INSERT\s+INTO\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
_UPDATE_TBL = re.compile(r"^UPDATE\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
_DELETE_FROM = re.compile(r"^DELETE\s+FROM\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)


def statement_family(statement: str) -> str:
    """Normalize one SQL statement to ``"<VERB> <table>"`` or a literal-free head.

    Savepoint names, bind placeholders and numeric/string literals are erased so
    transaction-control and driver noise land in stable buckets.
    """
    s = " ".join(statement.split())
    for pattern, verb in (
        (_SELECT_FROM, "SELECT"),
        (_INSERT_INTO, "INSERT"),
        (_UPDATE_TBL, "UPDATE"),
        (_DELETE_FROM, "DELETE"),
    ):
        m = pattern.match(s)
        if m:
            return f"{verb} {m.group(1).lower()}"
    # No table: transaction control, advisory locks, scalar SELECTs.
    head = re.sub(r"\$\d+|%\([^)]*\)s|'[^']*'|\b\d+\b", "?", s)
    return head[:60].strip().upper()


def is_read(family: str) -> bool:
    return family.startswith("SELECT ")


def is_write(family: str) -> bool:
    return family.startswith(("INSERT ", "UPDATE ", "DELETE "))


@contextmanager
def capture_sql(engine):
    """Collect every statement executed on ``engine`` inside the ``with`` body.

    Scoped deliberately tightly: the caller wraps *only* the HTTP call, so
    fixture setup and teardown can never contribute to a measurement.
    """
    statements: list[str] = []

    def _before(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _before)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before)


def families(statements: list[str]) -> Counter:
    return Counter(statement_family(s) for s in statements)


def report(endpoint: str, per_n: dict[int, Counter]) -> str:
    """A human-readable evidence table: one row per family, one column per N."""
    keys = sorted({k for c in per_n.values() for k in c})
    width = max([len(k) for k in keys] + [len("family")])
    head = f"{'family'.ljust(width)} | " + " | ".join(f"N={n:>3}" for n in per_n)
    lines = [f"\n=== {endpoint} ===", head, "-" * len(head)]
    for k in keys:
        lines.append(f"{k.ljust(width)} | " + " | ".join(f"{per_n[n][k]:>5}" for n in per_n))
    lines.append(f"{'TOTAL'.ljust(width)} | " + " | ".join(f"{sum(per_n[n].values()):>5}" for n in per_n))
    lines.append(f"{'TOTAL SELECT'.ljust(width)} | " + " | ".join(
        f"{sum(v for k, v in per_n[n].items() if is_read(k)):>5}" for n in per_n))
    lines.append(f"{'TOTAL WRITE'.ljust(width)} | " + " | ".join(
        f"{sum(v for k, v in per_n[n].items() if is_write(k)):>5}" for n in per_n))
    return "\n".join(lines)


def assert_flat(endpoint: str, per_n: dict[int, Counter], *, only=None) -> None:
    """Assert the selected statement families are identical at every cardinality.

    ``only`` filters to a predicate (e.g. reads); omitted, every family must be
    flat.  Exact equality is the standard because these handlers' query plans do
    not legitimately vary with row count — no tolerance is warranted, so none is
    granted.
    """
    ns = list(per_n)
    base_n = ns[0]

    def _sel(c: Counter) -> dict[str, int]:
        return {k: v for k, v in c.items() if (only is None or only(k))}

    base = _sel(per_n[base_n])
    for n in ns[1:]:
        current = _sel(per_n[n])
        if current != base:
            growing = sorted(
                {k for k in set(base) | set(current) if base.get(k, 0) != current.get(k, 0)}
            )
            detail = ", ".join(f"{k}: {base.get(k, 0)} → {current.get(k, 0)}" for k in growing)
            raise AssertionError(
                f"{endpoint}: query count is not independent of cardinality "
                f"(N={base_n} → N={n}); families that moved: {detail}"
                f"{report(endpoint, per_n)}"
            )


def assert_flat_and_empty_is_not_costlier(endpoint: str, per_n: dict[int, Counter]) -> None:
    """Two distinct properties, asserted separately because they are not the same.

    1. **Slope** — the query count must be identical across every NON-EMPTY
       volume. This is the N+1 property and it is asserted exactly.
    2. **The empty page** — measured too, so a fixture that silently returned
       nothing could not make (1) pass by accident. An empty page is allowed to
       issue FEWER queries (a handler may skip a name lookup when there are no
       rows to decorate); it must never issue more. Folding N=0 into (1) would
       report a legitimate short-circuit as an N+1 defect.
    """
    nonempty = {n: c for n, c in per_n.items() if n > 0}
    assert len(nonempty) >= 2, "slope needs at least two non-empty volumes"
    assert_flat(endpoint, nonempty)
    if 0 in per_n:
        empty_total = sum(per_n[0].values())
        smallest = sum(nonempty[min(nonempty)].values())
        assert empty_total <= smallest, (
            f"{endpoint}: the EMPTY page costs more than a populated one "
            f"({empty_total} > {smallest}){report(endpoint, per_n)}"
        )


# ── the live tier ───────────────────────────────────────────────────────────


def _pg_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


@pytest_asyncio.fixture
async def tier():
    """A model-built schema on the live test PostgreSQL, plus an ASGI client.

    Mirrors ``conftest.isolation_ctx``'s engine handling but hands the engine and
    session factory back so a test can seed its own cardinality and attach the
    statement listener.  Skips — never silently passes — without a reachable
    test database.
    """
    url = _pg_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the DB tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base, get_db
    from app.main import app

    engine = create_async_engine(url)
    # (QA-R-002) DB tier is mandatory once TEST_DATABASE_URL is set — setup errors
    # (refused connection, privilege, bad schema) must FAIL, never self-skip green.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            yield engine, Session, client
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def test_tr015_db_tier_is_a_real_postgres(tier) -> None:
    """Guard: every measurement below is worthless if this is not a live DB."""
    from sqlalchemy import text

    engine, _Session, _client = tier
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT current_database(), current_user, version()")
        )).one()
    assert "PostgreSQL" in row[2], row[2]
    assert row[0], "no current_database — the tier is not connected"
    print(f"\n[TR-015] DB tier: database={row[0]} user={row[1]} :: {row[2].split(',')[0]}")


# ── helpers shared by the endpoint measurements ─────────────────────────────


async def _seed_platform_admin(Session):
    """One super-admin user; returns (user_id, auth header)."""
    from app.core.security import issue_token
    from app.models import User
    from app.models.enums import PlatformRole

    async with Session() as s:
        u = User(telegram_id=910_001, platform_role=PlatformRole.super_admin)
        s.add(u)
        await s.flush()
        uid = u.id
        await s.commit()
    return uid, {"Authorization": f"Bearer {issue_token(user_id=str(uid))}"}


# ── 1. GET /api/auth/me — cardinality = tenant memberships ──────────────────


async def test_tr015_auth_me_query_count_is_flat_over_memberships(tier) -> None:
    """``GET /api/auth/me`` — the audit's "GET /me"; grows by memberships."""
    from app.core.security import issue_token
    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole, TenantStatus

    engine, Session, client = tier
    per_n: dict[int, Counter] = {}

    for n in CARDINALITIES:
        async with Session() as s:
            u = User(telegram_id=920_000 + n)
            s.add(u)
            await s.flush()
            for i in range(n):
                t = Tenant(
                    slug=f"tr015-me-{n}-{i}", name=f"T{n}-{i}",
                    plan_code="trial", status=TenantStatus.active,
                )
                s.add(t)
                await s.flush()
                s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner))
            uid = u.id
            await s.commit()

        headers = {"Authorization": f"Bearer {issue_token(user_id=str(uid))}"}
        with capture_sql(engine) as stmts:
            r = await client.get("/api/auth/me", headers=headers)
        assert r.status_code == 200, r.text[:300]
        assert len(r.json()["tenants"]) == n, "fixture did not reach the requested cardinality"
        per_n[n] = families(stmts)

    print(report("GET /api/auth/me", per_n))
    assert_flat("GET /api/auth/me", per_n)


# ── 2. GET /api/platform/referrals — cardinality = referral rows ────────────


async def test_tr015_platform_referrals_query_count_is_flat_over_rows(tier) -> None:
    """``GET /api/platform/referrals`` — grows by referral rows returned.

    Each referral points at a *distinct* referrer user and referee tenant, so a
    per-row name lookup would be visible as growth rather than hidden by reuse.
    """
    from app.models import Referral, Tenant, User
    from app.models.enums import TenantStatus

    engine, Session, client = tier
    _admin_id, headers = await _seed_platform_admin(Session)
    per_n: dict[int, Counter] = {}

    for n in CARDINALITIES:
        # Rebuild the table each round so exactly N rows are in scope; the
        # endpoint's default limit is 50, which covers the largest cardinality.
        async with Session() as s:
            from sqlalchemy import delete

            await s.execute(delete(Referral))
            for i in range(n):
                ref_user = User(telegram_id=930_000 + n * 1000 + i, first_name=f"Ref{i}")
                tn = Tenant(
                    slug=f"tr015-ref-{n}-{i}", name=f"Referee {n}-{i}",
                    plan_code="trial", status=TenantStatus.active,
                )
                s.add_all([ref_user, tn])
                await s.flush()
                s.add(Referral(
                    referrer_user_id=ref_user.id, referee_tenant_id=tn.id,
                    status="pending", credit_amount=Decimal("0"),
                ))
            await s.commit()

        with capture_sql(engine) as stmts:
            r = await client.get("/api/platform/referrals", headers=headers)
        assert r.status_code == 200, r.text[:300]
        assert len(r.json()["items"]) == n, "fixture did not reach the requested cardinality"
        per_n[n] = families(stmts)

    print(report("GET /api/platform/referrals", per_n))
    assert_flat("GET /api/platform/referrals", per_n)


# ── 3. GET /api/platform/overview — cardinality = underlying row volume ────


async def test_tr015_platform_overview_query_count_is_flat_over_volume(tier) -> None:
    """``GET /api/platform/overview`` — aggregates only; must not scale at all.

    The endpoint returns no collection, so the growing quantity is the volume it
    aggregates over: tenants, customers, installments and payments.
    """
    from datetime import UTC, date, datetime

    from app.models import Customer, Installment, Payment, Tenant
    from app.models.enums import (
        InstallmentStatus,
        PaymentMethod,
        PaymentStatus,
        TenantStatus,
    )

    engine, Session, client = tier
    _admin_id, headers = await _seed_platform_admin(Session)
    per_n: dict[int, Counter] = {}
    made = 0

    for n in CARDINALITIES:
        async with Session() as s:
            while made < n:
                i = made
                t = Tenant(
                    slug=f"tr015-ov-{i}", name=f"Ov {i}",
                    plan_code="trial", status=TenantStatus.active,
                )
                s.add(t)
                await s.flush()
                c = Customer(tenant_id=t.id, full_name=f"C{i}", phone=f"+9989{i:08d}")
                s.add(c)
                await s.flush()
                ins = Installment(currency="UZS",
                    tenant_id=t.id, customer_id=c.id, status=InstallmentStatus.active,
                    total_amount=Decimal("1200000"), down_payment=Decimal("0"),
                    principal=Decimal("1200000"), monthly_amount=Decimal("100000"),
                    months=12, start_date=date(2026, 1, 1),
                    next_due_date=date(2026, 2, 1), paid_count=0,
                    paid_amount=Decimal("0"), contract_number=f"MDD-2026-{i:06d}",
                )
                s.add(ins)
                await s.flush()
                s.add(Payment(currency="UZS",
                    tenant_id=t.id, installment_id=ins.id, customer_id=c.id,
                    amount=Decimal("100000"),
                    method=PaymentMethod.cash, status=PaymentStatus.approved,
                    approved_at=datetime.now(UTC),
                ))
                made += 1
            await s.commit()

        with capture_sql(engine) as stmts:
            r = await client.get("/api/platform/overview", headers=headers)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["tenants_total"] == n, "fixture did not reach the requested cardinality"
        per_n[n] = families(stmts)

    print(report("GET /api/platform/overview", per_n))
    assert_flat("GET /api/platform/overview", per_n)


# ── 4. POST /api/tenant/installments — cardinality = submitted line items ──
#
# What grows is the ``items`` list, and nothing else: the customer, tenant,
# guarantor list (empty), term, formula and per-unit price are identical at
# every cardinality.  ``InstallmentIn.items`` caps at 1000, so 50 is a valid
# request and no domain limit is bent to reach it.
#
# Every item references a distinct tracked product, which is the branch that
# calls ``inventory.record_movement`` per item — the shape the audit flagged.


async def _seed_tenant_for_create(Session, *, max_items: int):
    """One tenant, owner and approved customer, plus ``max_items`` products."""
    from app.core.security import issue_token
    from app.models import Customer, Product, Tenant, TenantUser, User
    from app.models.enums import TenantRole, TenantStatus

    async with Session() as s:
        t = Tenant(slug="tr015-create", name="Create Co",
                   plan_code="trial", status=TenantStatus.active)
        s.add(t)
        await s.flush()
        u = User(telegram_id=940_001)
        s.add(u)
        await s.flush()
        s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner))
        cust = Customer(tenant_id=t.id, full_name="Buyer", phone="+998901112233")
        s.add(cust)
        await s.flush()
        products = []
        for i in range(max_items):
            p = Product(
                tenant_id=t.id, name_uz=f"P{i}", base_price=Decimal("100000"),
                stock=10_000, sku=f"SKU-{i}",
            )
            s.add(p)
            products.append(p)
        await s.flush()
        ctx = {
            "tenant_id": str(t.id),
            "customer_id": str(cust.id),
            "product_ids": [str(p.id) for p in products],
            "headers": {
                "Authorization": f"Bearer {issue_token(user_id=str(u.id))}",
                "X-Tenant-Id": str(t.id),
            },
        }
        await s.commit()
    return ctx


def _create_body(ctx: dict, n: int) -> dict:
    """Identical contract at every N except the number of line items."""
    return {
        "customer_id": ctx["customer_id"],
        "months": 12,
        "down_payment": 0,
        "calc_formula": "none",
        "items": [
            {
                "product_id": ctx["product_ids"][i],
                "product_name_snapshot": f"P{i}",
                "qty": 1,
                "unit_price": 100000,
            }
            for i in range(n)
        ],
        "guarantor_ids": [],
    }


async def test_tr015_create_installment_query_count_is_flat_over_items(tier) -> None:
    """``POST /api/tenant/installments`` — no family may scale with item count.

    The read side is the defect this pins: the handler batch-selects the whole
    product set one block above the loop, so a per-item re-read of the same rows
    is avoidable, not inherent.  Reads *and* writes are asserted flat — with the
    per-item read gone there is no mid-loop autoflush either, so the child-row
    INSERTs coalesce into one batched statement each.  N rows are still written;
    the assertion is about round trips, not rows.
    """
    engine, Session, client = tier
    ctx = await _seed_tenant_for_create(Session, max_items=max(CARDINALITIES))
    per_n: dict[int, Counter] = {}

    for n in CARDINALITIES:
        with capture_sql(engine) as stmts:
            r = await client.post(
                "/api/tenant/installments",
                json=_create_body(ctx, n),
                headers=ctx["headers"],
            )
        assert r.status_code == 201, r.text[:300]
        per_n[n] = families(stmts)

    print(report("POST /api/tenant/installments", per_n))
    assert_flat("POST /api/tenant/installments", per_n)


async def test_tr015_create_installment_semantics_are_unchanged(tier) -> None:
    """Response, stock ledger and tenant isolation must survive the batching.

    Handing ``record_movement`` an already-loaded row instead of letting it
    re-read one is only safe if the outcome is identical, so this pins the
    outcome rather than the mechanism: contract totals, one item + one movement
    row per line, per-product stock decremented once **per line** (a product
    listed twice is decremented twice), and a foreign ``product_id`` still 404s.
    """
    from sqlalchemy import func, select

    from app.models import InstallmentItem, Product, StockMovement, Tenant
    from app.models.enums import TenantStatus

    _engine, Session, client = tier
    ctx = await _seed_tenant_for_create(Session, max_items=3)

    body = _create_body(ctx, 2)
    # Line 3 repeats line 1's product with qty 2 → that product must fall by 3.
    body["items"].append({
        "product_id": ctx["product_ids"][0],
        "product_name_snapshot": "P0",
        "qty": 2,
        "unit_price": 100000,
    })
    r = await client.post("/api/tenant/installments", json=body, headers=ctx["headers"])
    assert r.status_code == 201, r.text[:300]
    created = r.json()
    # 1 + 1 + 2 units × 100 000, interest-free ("none") and no down payment.
    assert created["total_amount"] == 400000, created
    assert created["down_payment"] == 0
    assert created["months"] == 12
    iid = created["id"]

    async with Session() as s:
        items = (await s.execute(
            select(func.count(InstallmentItem.id)).where(InstallmentItem.installment_id == iid)
        )).scalar_one()
        movements = (await s.execute(
            select(func.count(StockMovement.id)).where(
                StockMovement.related_installment_id == iid
            )
        )).scalar_one()
        stock = {
            str(p.id): p.stock
            for p in (await s.execute(select(Product))).scalars()
        }
    assert items == 3, "one InstallmentItem per submitted line"
    assert movements == 3, "one StockMovement per tracked line, repeats included"
    assert stock[ctx["product_ids"][0]] == 10_000 - 3, "repeated product decremented per line"
    assert stock[ctx["product_ids"][1]] == 10_000 - 1
    assert stock[ctx["product_ids"][2]] == 10_000, "untouched product unchanged"

    # Tenant isolation: a product owned by another tenant is still not plantable.
    async with Session() as s:
        other = Tenant(slug="tr015-other", name="Other Co",
                       plan_code="trial", status=TenantStatus.active)
        s.add(other)
        await s.flush()
        foreign = Product(tenant_id=other.id, name_uz="Foreign",
                          base_price=Decimal("1"), stock=5, sku="FOREIGN")
        s.add(foreign)
        await s.flush()
        foreign_id = str(foreign.id)
        await s.commit()

    bad = _create_body(ctx, 1)
    bad["items"][0]["product_id"] = foreign_id
    r = await client.post("/api/tenant/installments", json=bad, headers=ctx["headers"])
    assert r.status_code == 404, r.text[:300]
    assert r.json()["detail"] == "product_not_found"

    async with Session() as s:
        untouched = (await s.execute(
            select(Product.stock).where(Product.id == foreign_id)
        )).scalar_one()
    assert untouched == 5, "a rejected create must not move another tenant's stock"


# ── REL-004: the four hot KEEP tenant reads ─────────────────────────────────
#
# Phase 7 recorded that slope protection existed for only four endpoints, none
# of them the surfaces a shop actually loads all day. These four close that gap.
# `0` is measured alongside the non-empty volumes: an empty page that short-circuits
# a lookup would otherwise let "flat" be an accident of the fixture.

#: Volumes for the 50-row-default endpoints; 50 fills the default page exactly.
KEEP_CARDINALITIES = (0, 1, 10, 50)
#: `/tenant/worklist` defaults to limit=20, so a full page is 20.
WORKLIST_CARDINALITIES = (0, 1, 10, 20)


async def _seed_tenant_owner(Session, *, slug: str, tg: int):
    """A tenant with one owner member. Returns (tenant_id, auth headers)."""
    from app.core.security import issue_token
    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole, TenantStatus

    async with Session() as s:
        t = Tenant(slug=slug, name=slug, plan_code="trial", status=TenantStatus.active)
        u = User(telegram_id=tg)
        s.add_all([t, u])
        await s.flush()
        s.add(TenantUser(tenant_id=t.id, user_id=u.id, role=TenantRole.owner))
        tid = t.id
        await s.commit()
    return tid, {"Authorization": f"Bearer {issue_token(user_id=str(u.id))}"}


async def _reset_customers(Session, tenant_id, n: int, *, prefix: str):
    """Leave exactly ``n`` customers on ``tenant_id``."""
    from sqlalchemy import delete

    from app.models import Customer
    from app.models.enums import CustomerStatus

    async with Session() as s:
        await s.execute(delete(Customer).where(Customer.tenant_id == tenant_id))
        for i in range(n):
            s.add(Customer(
                tenant_id=tenant_id, full_name=f"{prefix} {i}",
                phone=f"+9989{tenant_id.int % 10}{i:07d}", status=CustomerStatus.active,
            ))
        await s.commit()


async def test_tr015_tenant_customers_query_count_is_flat(tier) -> None:
    """``GET /api/tenant/customers`` — the shop's most-loaded list."""
    engine, Session, client = tier
    tid, headers = await _seed_tenant_owner(Session, slug="tr015-cust-a", tg=940_101)
    # A second tenant with its own rows: isolation must not change the count.
    other_tid, _ = await _seed_tenant_owner(Session, slug="tr015-cust-b", tg=940_102)
    await _reset_customers(Session, other_tid, 25, prefix="Other")

    per_n: dict[int, Counter] = {}
    for n in KEEP_CARDINALITIES:
        await _reset_customers(Session, tid, n, prefix="Cust")
        with capture_sql(engine) as stmts:
            r = await client.get("/api/tenant/customers", headers=headers)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert len(body["items"]) == n, "fixture did not reach the requested cardinality"
        assert body["total"] == n, "total must describe this tenant only"
        assert all("Other" not in c["full_name"] for c in body["items"]), "cross-tenant leak"
        per_n[n] = families(stmts)

    print(report("GET /api/tenant/customers", per_n))
    assert_flat_and_empty_is_not_costlier("GET /api/tenant/customers", per_n)


async def _reset_installments(Session, tenant_id, n: int, *, due_offset_days: int | None = None):
    """Leave exactly ``n`` installments (each with its own customer) on ``tenant_id``.

    A distinct customer per contract matters: a per-row customer lookup would
    otherwise be hidden by reuse of one cached customer id.
    """
    from datetime import date, timedelta
    from decimal import Decimal as D

    from sqlalchemy import delete

    from app.models import Customer, Installment
    from app.models.enums import CustomerStatus, InstallmentStatus

    async with Session() as s:
        await s.execute(delete(Installment).where(Installment.tenant_id == tenant_id))
        await s.execute(delete(Customer).where(Customer.tenant_id == tenant_id))
        for i in range(n):
            c = Customer(
                tenant_id=tenant_id, full_name=f"Borrower {i}",
                phone=f"+9987{tenant_id.int % 10}{i:07d}", status=CustomerStatus.active,
            )
            s.add(c)
            await s.flush()
            due = None if due_offset_days is None else date.today() + timedelta(days=due_offset_days)
            s.add(Installment(currency="UZS",
                tenant_id=tenant_id, customer_id=c.id, status=InstallmentStatus.active,
                total_amount=D("1000000"), down_payment=D("0"), principal=D("1000000"),
                calc_formula="none", months=12, monthly_amount=D("90000"),
                paid_count=0, paid_amount=D("0"), start_date=date.today(),
                next_due_date=due,
            ))
        await s.commit()


async def test_tr015_tenant_installments_query_count_is_flat(tier) -> None:
    """``GET /api/tenant/installments`` — the contracts list."""
    engine, Session, client = tier
    tid, headers = await _seed_tenant_owner(Session, slug="tr015-inst-a", tg=940_201)
    other_tid, _ = await _seed_tenant_owner(Session, slug="tr015-inst-b", tg=940_202)
    await _reset_installments(Session, other_tid, 25)

    per_n: dict[int, Counter] = {}
    for n in KEEP_CARDINALITIES:
        await _reset_installments(Session, tid, n)
        with capture_sql(engine) as stmts:
            r = await client.get("/api/tenant/installments", headers=headers)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert len(body["items"]) == n, "fixture did not reach the requested cardinality"
        assert body["total"] == n, "total must describe this tenant only"
        per_n[n] = families(stmts)

    print(report("GET /api/tenant/installments", per_n))
    assert_flat_and_empty_is_not_costlier("GET /api/tenant/installments", per_n)


async def test_tr015_tenant_worklist_query_count_is_flat(tier) -> None:
    """``GET /api/tenant/worklist`` — the collections queue.

    This is the route the KEEP collections surface actually calls
    (`InstallmentsPage` via ``useTenantWorklistPages``, and `OverviewPage` via
    ``useTenantWorklist``) — not a page of ``/installments``. Contracts are seeded
    overdue so they land in the queue, and its default page is 20.

    The handler's docstring claims "one IN-query over the returned page, so there
    is no N+1". This measures that claim rather than trusting it.
    """
    engine, Session, client = tier
    tid, headers = await _seed_tenant_owner(Session, slug="tr015-work-a", tg=940_301)
    other_tid, _ = await _seed_tenant_owner(Session, slug="tr015-work-b", tg=940_302)
    await _reset_installments(Session, other_tid, 25, due_offset_days=-3)

    per_n: dict[int, Counter] = {}
    for n in WORKLIST_CARDINALITIES:
        await _reset_installments(Session, tid, n, due_offset_days=-3)  # overdue → in queue
        with capture_sql(engine) as stmts:
            r = await client.get("/api/tenant/worklist", headers=headers)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert len(body["items"]) == n, "fixture did not reach the requested cardinality"
        assert body["total"] == n, "total must describe this tenant's queue only"
        per_n[n] = families(stmts)

    print(report("GET /api/tenant/worklist", per_n))
    assert_flat_and_empty_is_not_costlier("GET /api/tenant/worklist", per_n)


async def _reset_payments(Session, tenant_id, n: int):
    """Leave exactly ``n`` payments on ``tenant_id``, each on its own contract."""
    from datetime import date
    from decimal import Decimal as D

    from sqlalchemy import delete

    from app.models import Customer, Installment, Payment
    from app.models.enums import CustomerStatus, InstallmentStatus, PaymentMethod, PaymentStatus

    async with Session() as s:
        await s.execute(delete(Payment).where(Payment.tenant_id == tenant_id))
        await s.execute(delete(Installment).where(Installment.tenant_id == tenant_id))
        await s.execute(delete(Customer).where(Customer.tenant_id == tenant_id))
        for i in range(n):
            c = Customer(
                tenant_id=tenant_id, full_name=f"Payer {i}",
                phone=f"+9986{tenant_id.int % 10}{i:07d}", status=CustomerStatus.active,
            )
            s.add(c)
            await s.flush()
            ins = Installment(currency="UZS",
                tenant_id=tenant_id, customer_id=c.id, status=InstallmentStatus.active,
                total_amount=D("1000000"), down_payment=D("0"), principal=D("1000000"),
                calc_formula="none", months=12, monthly_amount=D("90000"),
                paid_count=0, paid_amount=D("0"), start_date=date.today(),
            )
            s.add(ins)
            await s.flush()
            s.add(Payment(currency="UZS",
                tenant_id=tenant_id, installment_id=ins.id, customer_id=c.id,
                amount=D("90000"), method=PaymentMethod.cash, status=PaymentStatus.approved,
            ))
        await s.commit()


async def test_tr015_tenant_payments_query_count_is_flat(tier) -> None:
    """``GET /api/tenant/payments`` — the money ledger.

    Every payment sits on its own contract and customer, so a per-row customer or
    contract-number lookup would show as growth rather than collapse onto one id.
    """
    engine, Session, client = tier
    tid, headers = await _seed_tenant_owner(Session, slug="tr015-pay-a", tg=940_401)
    other_tid, _ = await _seed_tenant_owner(Session, slug="tr015-pay-b", tg=940_402)
    await _reset_payments(Session, other_tid, 25)

    per_n: dict[int, Counter] = {}
    for n in KEEP_CARDINALITIES:
        await _reset_payments(Session, tid, n)
        with capture_sql(engine) as stmts:
            r = await client.get("/api/tenant/payments", headers=headers)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert len(body["items"]) == n, "fixture did not reach the requested cardinality"
        assert body["total"] == n, "total must describe this tenant only"
        per_n[n] = families(stmts)

    print(report("GET /api/tenant/payments", per_n))
    assert_flat_and_empty_is_not_costlier("GET /api/tenant/payments", per_n)
