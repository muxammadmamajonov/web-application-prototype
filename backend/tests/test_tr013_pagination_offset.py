"""TR-013 — growth-bearing list reads must be able to reach rows past page 1.

A ``limit`` with no ``offset`` is a truncating cap, not pagination: once a tenant
has more rows than the cap, the older ones are retained but permanently
unviewable. These are DB-backed contract tests over the real endpoints; each one
pins, per endpoint:

* the default page (no query params) is unchanged, and ``offset=0`` returns it
  byte-for-byte — the additive parameter cannot alter existing callers;
* a later page is reachable, and adjacent pages neither overlap nor skip;
* ordering is deterministic. ``created_at`` is a ``server_default=func.now()``
  transaction timestamp, so rows seeded in one commit tie exactly — without a
  unique tiebreaker the sort is arbitrary and paging silently drops rows;
* ``offset`` is bounded (negative rejected) and existing ``limit`` bounds hold;
* authorization and tenant scoping are untouched.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

# Rows per fixture: enough that a 3-row page leaves a reachable tail.
SEED_N = 7
PAGE = 3
BASE_TS = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def paging_ctx(isolation_ctx):
    """``isolation_ctx`` plus a session factory on the same schema, for seeding
    rows that have no create endpoint (notifications, SMS log, top-ups)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    client, ctx = isolation_ctx
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    try:
        yield client, ctx, async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _tenant_auth(ctx: dict) -> dict:
    return {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}


async def _platform_auth(Session) -> dict:
    """A super-admin token. ``isolation_ctx``'s users are tenant-only."""
    from app.core.security import issue_token
    from app.models import User
    from app.models.enums import PlatformRole

    async with Session() as s:
        u = User(telegram_id=900001, platform_role=PlatformRole.super_admin)
        s.add(u)
        await s.flush()
        uid = str(u.id)
        await s.commit()
    return {"Authorization": f"Bearer {issue_token(user_id=uid)}"}


# ── per-endpoint descriptors ─────────────────────────────────────────────────
#
# ``seed`` writes ``n`` rows for tenant/user A, ascending in time from BASE_TS
# when ``tie`` is False and all at BASE_TS when True (the natural single-commit
# case), and RETURNS the ids in insertion order. With distinct timestamps the
# expected newest-first order is therefore ``reversed(seeded)`` — an expectation
# derived from the fixture, not from re-running the endpoint's own query, so a
# page boundary can be checked against the whole ordered set without slicing any
# server response client-side. ``extract`` pulls the id list out of whatever
# envelope the endpoint returns.


async def _commit_ids(Session, rows) -> list[str]:
    async with Session() as s:
        s.add_all(rows)
        await s.flush()
        ids = [str(r.id) for r in rows]
        await s.commit()
    return ids


def _stamp(i: int, *, tie: bool):
    return BASE_TS if tie else BASE_TS + timedelta(minutes=i)


async def _seed_tenant_topups(Session, ctx, n, *, tie, tenant_key="tenant_a"):
    from decimal import Decimal

    from app.models import TenantTopup

    return await _commit_ids(Session, [
        TenantTopup(
            tenant_id=ctx[tenant_key], amount=Decimal(1000 + i), method="card",
            status="pending", created_at=_stamp(i, tie=tie),
        ) for i in range(n)
    ])


async def _seed_broadcasts(Session, ctx, n, *, tie, tenant_id_key="tenant_a"):
    from app.models import Broadcast

    return await _commit_ids(Session, [
        Broadcast(
            tenant_id=None if tenant_id_key is None else ctx[tenant_id_key],
            title=f"B{i}", body_uz=f"body {i}", created_at=_stamp(i, tie=tie),
        ) for i in range(n)
    ])


async def _seed_platform_broadcasts(Session, ctx, n, *, tie):
    return await _seed_broadcasts(Session, ctx, n, tie=tie, tenant_id_key=None)


async def _seed_notifications(Session, ctx, n, *, tie):
    from app.models import Notification

    return await _commit_ids(Session, [
        Notification(
            user_id=ctx["user_a"], kind="reminder", title=f"N{i}",
            created_at=_stamp(i, tie=tie),
        ) for i in range(n)
    ])


async def _seed_sms(Session, ctx, n, *, tie):
    from app.models import SmsMessage

    return await _commit_ids(Session, [
        SmsMessage(
            tenant_id=ctx["tenant_a"], phone="+998900000001", text=f"msg {i}",
            created_at=_stamp(i, tie=tie),
        ) for i in range(n)
    ])


def _items(payload) -> list[str]:
    rows = payload if isinstance(payload, list) else payload["items"]
    return [r["id"] for r in rows]


TENANT_TOPUPS = dict(
    path="/api/tenant/billing/topups", seed=_seed_tenant_topups,
    extract=_items, default_limit=50, limit_param=None, desc=True,
)
PLATFORM_BROADCASTS = dict(
    path="/api/platform/broadcasts", seed=_seed_platform_broadcasts,
    extract=_items, default_limit=100, limit_param=None, desc=True, platform=True,
)
TENANT_BROADCASTS = dict(
    path="/api/tenant/broadcasts", seed=_seed_broadcasts,
    extract=_items, default_limit=100, limit_param=None, desc=True,
)
NOTIFICATIONS = dict(
    path="/api/me/notifications", seed=_seed_notifications,
    extract=_items, default_limit=30, limit_param="limit", limit_max=100,
    desc=True, no_tenant_header=True,
)
SMS_HISTORY = dict(
    path="/api/tenant/sms/history", seed=_seed_sms,
    extract=_items, default_limit=50, limit_param="limit", limit_max=200, desc=True,
)
PLATFORM_TOPUPS = dict(
    path="/api/platform/topups", seed=_seed_tenant_topups,
    extract=_items, default_limit=200, limit_param=None, desc=True, platform=True,
)

ENDPOINTS = [
    pytest.param(TENANT_TOPUPS, id="tenant-billing-topups"),
    pytest.param(PLATFORM_BROADCASTS, id="platform-broadcasts"),
    pytest.param(TENANT_BROADCASTS, id="tenant-broadcasts"),
    pytest.param(NOTIFICATIONS, id="me-notifications"),
    pytest.param(SMS_HISTORY, id="sms-history"),
    pytest.param(PLATFORM_TOPUPS, id="platform-topups"),
]

#: Endpoints whose page size is a hard-coded cap with no caller-facing `limit`.
#: Their real page boundary is that cap, so proving adjacency needs a fixture
#: larger than it — see `test_fixed_cap_pages_are_disjoint_at_the_real_boundary`.
FIXED_CAP_ENDPOINTS = [
    pytest.param(TENANT_TOPUPS, id="tenant-billing-topups"),
    pytest.param(PLATFORM_BROADCASTS, id="platform-broadcasts"),
    pytest.param(TENANT_BROADCASTS, id="tenant-broadcasts"),
    pytest.param(PLATFORM_TOPUPS, id="platform-topups"),
]

#: Endpoints that expose `limit`, so a small page can be requested on the wire.
PAGED_ENDPOINTS = [
    pytest.param(NOTIFICATIONS, id="me-notifications"),
    pytest.param(SMS_HISTORY, id="sms-history"),
]

#: Rows past the cap in the boundary fixture — enough for a short second page.
TAIL = 3


async def _auth_for(spec, ctx, Session) -> dict:
    if spec.get("platform"):
        return await _platform_auth(Session)
    if spec.get("no_tenant_header"):
        return ctx["auth_a"]
    return _tenant_auth(ctx)


async def _get(client, spec, auth, **params):
    r = await client.get(spec["path"], headers=auth, params=params or None)
    assert r.status_code == 200, f"{spec['path']}: {r.status_code} {r.text[:300]}"
    return r.json()


# ── the invariant battery ────────────────────────────────────────────────────

@pytest.mark.parametrize("spec", ENDPOINTS)
async def test_default_page_is_unchanged_and_offset_zero_is_identical(spec, paging_ctx) -> None:
    """The additive parameter must be invisible to every existing caller."""
    client, ctx, Session = paging_ctx
    auth = await _auth_for(spec, ctx, Session)
    await spec["seed"](Session, ctx, SEED_N, tie=False)

    default = await _get(client, spec, auth)
    explicit_zero = await _get(client, spec, auth, offset=0)

    assert explicit_zero == default, "offset=0 must reproduce the no-parameter response exactly"
    # SEED_N < every default limit here, so the default page is the whole set,
    # newest first — the pre-change contract.
    assert len(spec["extract"](default)) == SEED_N


@pytest.mark.parametrize("spec", PAGED_ENDPOINTS)
async def test_later_page_is_reachable_without_overlap_or_gap(spec, paging_ctx) -> None:
    """The finding itself, for the endpoints that expose ``limit``: a small page
    is requested ON THE WIRE, so each response *is* the page under test."""
    client, ctx, Session = paging_ctx
    auth = await _auth_for(spec, ctx, Session)
    seeded = await spec["seed"](Session, ctx, SEED_N, tie=False)
    expected = list(reversed(seeded))          # distinct timestamps → newest first

    assert spec["extract"](await _get(client, spec, auth)) == expected

    paged: list[str] = []
    for start in range(0, SEED_N, PAGE):
        page = spec["extract"](await _get(client, spec, auth, offset=start, limit=PAGE))
        assert len(page) == min(PAGE, SEED_N - start), "server did not honour the page size"
        paged.extend(page)

    assert len(paged) == len(set(paged)), "adjacent pages overlap — a row was served twice"
    assert paged == expected, "walking the offsets must reproduce the full ordered set, no gap"


@pytest.mark.parametrize("spec", FIXED_CAP_ENDPOINTS)
async def test_fixed_cap_pages_are_disjoint_at_the_real_boundary(spec, paging_ctx) -> None:
    """These endpoints have no caller-facing ``limit``: their page size is the
    hard-coded cap (50/100/200). The page boundary therefore only exists past
    that cap, and it must be checked against the COMPLETE server responses —
    slicing a response client-side would compare fabricated pages, not the ones
    the server actually served, and would hide a genuine overlap."""
    client, ctx, Session = paging_ctx
    auth = await _auth_for(spec, ctx, Session)
    cap = spec["default_limit"]
    seeded = await spec["seed"](Session, ctx, cap + TAIL, tie=False)
    expected = list(reversed(seeded))          # distinct timestamps → newest first

    page1 = spec["extract"](await _get(client, spec, auth, offset=0))
    page2 = spec["extract"](await _get(client, spec, auth, offset=cap))

    assert len(page1) == cap, f"page 1 must be a full cap page, got {len(page1)}"
    assert len(page2) == TAIL, f"page 2 must be the remaining tail, got {len(page2)}"
    assert not set(page1) & set(page2), "the two server pages overlap — a row was served twice"
    assert page1 + page2 == expected, (
        "the two whole server responses must concatenate to the full deterministic order"
    )
    # The tail is unreachable without offset: it is not on page 1 by construction.
    assert set(page2).isdisjoint(spec["extract"](await _get(client, spec, auth)))


@pytest.mark.parametrize("spec", ENDPOINTS)
async def test_ordering_is_deterministic_when_sort_keys_tie(spec, paging_ctx) -> None:
    """Rows written in one commit share ``created_at`` exactly (server_default
    now() is the transaction timestamp), so the business sort key alone cannot
    order them. A unique tiebreaker must decide, or paging drops rows."""
    client, ctx, Session = paging_ctx
    auth = await _auth_for(spec, ctx, Session)
    await spec["seed"](Session, ctx, SEED_N, tie=True)

    ids = spec["extract"](await _get(client, spec, auth))
    assert len(ids) == SEED_N
    assert ids == sorted(ids, reverse=spec["desc"]), (
        "tied rows must fall back to the unique id tiebreaker; "
        "an arbitrary order here means offset paging can duplicate or skip rows"
    )


@pytest.mark.parametrize("spec", ENDPOINTS)
async def test_negative_offset_is_rejected(spec, paging_ctx) -> None:
    client, ctx, Session = paging_ctx
    auth = await _auth_for(spec, ctx, Session)

    r = await client.get(spec["path"], headers=auth, params={"offset": -1})
    assert r.status_code == 422, (
        f"offset=-1 returned {r.status_code}; an unvalidated offset is either "
        "ignored (no pagination) or reaches SQL"
    )


@pytest.mark.parametrize("spec", ENDPOINTS)
async def test_existing_limit_bounds_are_unchanged(spec, paging_ctx) -> None:
    """Whatever the endpoint did with ``limit`` before, it still does."""
    client, ctx, Session = paging_ctx
    auth = await _auth_for(spec, ctx, Session)
    await spec["seed"](Session, ctx, SEED_N, tie=False)

    if spec["limit_param"] is None:
        # No caller-facing limit today; adding offset must not introduce one.
        r = await client.get(spec["path"], headers=auth, params={"limit": 1})
        assert r.status_code == 200
        assert len(spec["extract"](r.json())) == SEED_N, "a limit param was added — not in scope"
        return

    small = spec["extract"](await _get(client, spec, auth, limit=2))
    assert len(small) == 2
    # (MUDD-66 LOW-5) These two used to CLAMP an over-limit request by hand and
    # answer 200 with a different page than the one asked for. The bound itself is
    # unchanged (100 / 200) — it is now DECLARED on the parameter, like the other
    # seventeen list routes, so an out-of-range page size is refused instead of
    # silently rewritten. No shipped client sends one (`useSmsHistory` defaults to
    # 50; `/me/notifications` sends no limit at all).
    r = await client.get(
        spec["path"], headers=auth, params={"limit": spec["limit_max"] + 5_000},
    )
    assert r.status_code == 422, (
        f"{spec['path']}: an over-limit page size returned {r.status_code}; "
        "the route re-capped it by hand instead of refusing"
    )
    at_max = spec["extract"](await _get(client, spec, auth, limit=spec["limit_max"]))
    assert len(at_max) == SEED_N, "the declared maximum must still be accepted"


@pytest.mark.parametrize("spec", ENDPOINTS)
async def test_authorization_is_unchanged(spec, paging_ctx) -> None:
    client, ctx, _Session = paging_ctx

    anon = await client.get(spec["path"], params={"offset": 0})
    assert anon.status_code == 401, f"unauthenticated access returned {anon.status_code}"

    if spec.get("platform"):
        # A tenant-only user must not reach a platform queue, paged or not.
        r = await client.get(spec["path"], headers=_tenant_auth(ctx), params={"offset": 0})
        assert r.status_code == 403


async def test_tenant_topups_offset_cannot_cross_tenants(paging_ctx) -> None:
    """Tenant scoping survives paging — B's top-ups never appear in A's pages,
    and an offset past A's own rows returns nothing rather than spilling over."""
    client, ctx, Session = paging_ctx
    a_ids = await _seed_tenant_topups(Session, ctx, SEED_N, tie=False)
    b_ids = await _seed_tenant_topups(Session, ctx, SEED_N, tie=False, tenant_key="tenant_b")
    a_auth, b_auth = _tenant_auth(ctx), {**ctx["auth_b"], "X-Tenant-Id": ctx["tenant_b"]}

    first = _items(await _get(client, TENANT_TOPUPS, a_auth, offset=0))
    assert set(first) == set(a_ids), "page 1 must be exactly tenant A's rows"
    assert not set(first) & set(b_ids)

    # Offset past everything A owns: empty, not the next tenant's rows.
    assert _items(await _get(client, TENANT_TOPUPS, a_auth, offset=SEED_N)) == []
    assert set(_items(await _get(client, TENANT_TOPUPS, b_auth, offset=0))) == set(b_ids)


BROADCAST_ENDPOINTS = [
    pytest.param(TENANT_BROADCASTS, id="tenant-broadcasts"),
    pytest.param(PLATFORM_BROADCASTS, id="platform-broadcasts"),
]


@pytest.mark.parametrize("spec", BROADCAST_ENDPOINTS)
async def test_broadcast_total_still_describes_the_returned_page(spec, paging_ctx) -> None:
    """Both broadcast endpoints return ``{"items": [...], "total": len(rows)}``.
    ``total`` is the page's OWN length — it was never a filtered count, and was
    already capped at 100 before this pass. Adding offset must not silently
    redefine it, on the first page or any later one."""
    client, ctx, Session = paging_ctx
    auth = await _auth_for(spec, ctx, Session)
    cap = spec["default_limit"]
    await spec["seed"](Session, ctx, cap + TAIL, tie=False)

    page1 = await _get(client, spec, auth, offset=0)
    page2 = await _get(client, spec, auth, offset=cap)

    assert page1["total"] == len(page1["items"]) == cap
    assert page2["total"] == len(page2["items"]) == TAIL, (
        "total must keep meaning 'rows in THIS response' — not a filtered count "
        "silently introduced by paging"
    )


# ── the invoice history, which already had offset ────────────────────────────

async def test_invoice_history_pages_stably_when_issued_at_ties(paging_ctx) -> None:
    """``GET /tenant/invoices`` is the tenant's real invoice history and already
    carried ``limit``+``offset``. Its sort key is not unique, though: one billing
    run — a backfill, or a catch-up over several unbilled periods — stamps every
    invoice it issues with the same ``issued_at``, and offset paging over a tied
    key duplicates and skips rows. ``uq_invoice_tenant_period`` bounds a tenant to
    one invoice per period, so the periods differ here while the run does not."""
    from datetime import date
    from decimal import Decimal

    from app.models import Invoice

    client, ctx, Session = paging_ctx
    issued = BASE_TS
    async with Session() as s:
        for i in range(SEED_N):
            s.add(Invoice(
                tenant_id=ctx["tenant_a"], plan_code="trial",
                period_start=date(2026, i + 1, 1), period_end=date(2026, i + 1, 28),
                amount=Decimal(1000 + i), issued_at=issued,
                due_at=issued + timedelta(days=14),
            ))
        await s.commit()

    auth = _tenant_auth(ctx)
    full = _items(await _get(client, {"path": "/api/tenant/invoices"}, auth))
    assert len(full) == SEED_N

    walked: list[str] = []
    for start in range(0, SEED_N, PAGE):
        walked.extend(_items(await _get(
            client, {"path": "/api/tenant/invoices"}, auth, offset=start, limit=PAGE)))

    assert len(walked) == len(set(walked)), "invoice pages overlap on a tied issued_at"
    assert walked == full, "walking the offsets must reproduce the full ordered set"


# ── nested collections on the installment detail ─────────────────────────────

async def _contract_with_payments(client, Session, ctx, n: int, *, tie: bool) -> str:
    from decimal import Decimal

    from app.models import Payment
    from app.models.enums import PaymentMethod, PaymentStatus

    auth = _tenant_auth(ctx)
    r = await client.post("/api/tenant/installments", headers=auth, json={
        "customer_id": ctx["customer_a"], "months": 6,
        "items": [{"product_name_snapshot": "Phone", "unit_price": "6000000", "qty": 1}],
    })
    assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"
    iid = r.json()["id"]

    async with Session() as s:
        for i in range(n):
            s.add(Payment(currency="UZS",
                tenant_id=ctx["tenant_a"], installment_id=iid, customer_id=ctx["customer_a"],
                amount=Decimal(1000 + i), method=PaymentMethod.cash,
                status=PaymentStatus.approved,
                created_at=BASE_TS if tie else BASE_TS + timedelta(minutes=i),
            ))
        await s.commit()
    return iid


async def _detail(client, ctx, iid, **params):
    r = await client.get(f"/api/tenant/installments/{iid}", headers=_tenant_auth(ctx),
                         params=params or None)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    return r.json()


async def test_nested_payments_default_response_is_unchanged(paging_ctx) -> None:
    client, ctx, Session = paging_ctx
    iid = await _contract_with_payments(client, Session, ctx, SEED_N, tie=False)

    default = await _detail(client, ctx, iid)
    assert await _detail(client, ctx, iid, payments_offset=0) == default
    assert len(default["payments"]) == SEED_N
    # Unrelated fields must not move.
    assert len(default["items"]) == 1
    assert default["guarantors"] == []


async def test_nested_payments_later_page_is_reachable(paging_ctx) -> None:
    """Payments are genuinely unbounded per contract: partial payments, rejected
    attempts and one late-fee row per overdue day all accumulate here."""
    client, ctx, Session = paging_ctx
    iid = await _contract_with_payments(client, Session, ctx, SEED_N, tie=False)

    full = [p["id"] for p in (await _detail(client, ctx, iid))["payments"]]
    walked: list[str] = []
    for start in range(0, SEED_N, PAGE):
        page = await _detail(client, ctx, iid, payments_offset=start, payments_limit=PAGE)
        walked.extend(p["id"] for p in page["payments"])

    assert len(walked) == len(set(walked)), "payment pages overlap"
    assert walked == full, "walking payments_offset must reproduce the full ordered set"


async def test_nested_payments_ordering_is_deterministic_when_tied(paging_ctx) -> None:
    client, ctx, Session = paging_ctx
    iid = await _contract_with_payments(client, Session, ctx, SEED_N, tie=True)

    ids = [p["id"] for p in (await _detail(client, ctx, iid))["payments"]]
    assert len(ids) == SEED_N
    assert ids == sorted(ids), "tied payments must fall back to the unique id tiebreaker"


async def test_nested_payments_negative_offset_is_rejected(paging_ctx) -> None:
    client, ctx, Session = paging_ctx
    iid = await _contract_with_payments(client, Session, ctx, 1, tie=False)

    r = await client.get(f"/api/tenant/installments/{iid}",
                         headers=_tenant_auth(ctx), params={"payments_offset": -1})
    assert r.status_code == 422


async def test_installment_line_items_stay_whole(paging_ctx) -> None:
    """``items`` is hard-bounded at 1000 per contract by ``InstallmentIn``
    (``Field(max_length=_MAX_ITEMS)``) on both write paths, so it is NOT paged —
    a caller must keep receiving every line item of the contract it asked for."""
    client, ctx, _Session = paging_ctx
    auth = _tenant_auth(ctx)
    r = await client.post("/api/tenant/installments", headers=auth, json={
        "customer_id": ctx["customer_a"], "months": 6,
        "items": [
            {"product_name_snapshot": f"Item {i}", "unit_price": "100000", "qty": 1}
            for i in range(12)
        ],
    })
    assert r.status_code == 201, f"{r.status_code} {r.text[:300]}"

    detail = await _detail(client, ctx, r.json()["id"])
    assert len(detail["items"]) == 12
    assert "items_offset" not in str(detail)


async def test_installment_detail_authorization_is_unchanged(paging_ctx) -> None:
    client, ctx, Session = paging_ctx
    iid = await _contract_with_payments(client, Session, ctx, 2, tie=False)

    anon = await client.get(f"/api/tenant/installments/{iid}", params={"payments_offset": 0})
    assert anon.status_code == 401

    b_auth = {**ctx["auth_b"], "X-Tenant-Id": ctx["tenant_b"]}
    cross = await client.get(f"/api/tenant/installments/{iid}", headers=b_auth,
                             params={"payments_offset": 0})
    assert cross.status_code == 404, "tenant B must not read tenant A's contract via any page"
