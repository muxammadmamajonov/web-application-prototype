"""Phase 3 remediation R-02 — collections worklist correctness.

The first Phase 3 pass moved the queue server-side but left four real defects,
each of which quietly misstates a shop's work:

  * "today" came from UTC, so at UTC+5 a payment due today was missing from the
    day's work for the last five hours of every day;
  * overdue-ness was read off the `status` column alone, so a contract whose due
    date had passed but which the nightly `mark_overdue` job had not yet flipped
    was counted as *due today* — understating the overdue figure a shop chases
    first;
  * ordering had no final tie-breaker, so rows sharing a status and a due date
    could appear on two pages or on none under OFFSET/LIMIT;
  * the caller could only ever see the first page.

These tests pin the corrected semantics against a real database.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.installments import add_months


async def _customer(client, auth, name: str, phone: str) -> str:
    r = await client.post("/api/tenant/customers", headers=auth,
                          json={"full_name": name, "phone": phone})
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    return r.json()["id"]


async def _issue(client, auth, cid: str, *, start: date, months: int = 6) -> str:
    r = await client.post(
        "/api/tenant/installments", headers=auth,
        json={
            "customer_id": cid, "months": months, "start_date": start.isoformat(),
            "items": [{"product_name_snapshot": "Phone", "unit_price": "6000000", "qty": 1}],
        },
    )
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    return r.json()["id"]


async def _worklist(client, auth, **params) -> dict:
    r = await client.get("/api/tenant/worklist", headers=auth, params=params or None)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    return r.json()


# ── reachability ─────────────────────────────────────────────────────────────

async def test_every_row_is_reachable_beyond_the_first_page(isolation_ctx) -> None:
    """More than one page of due work, all of it retrievable."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    past = date.today() - timedelta(days=40)
    cid = await _customer(client, auth, "Many Due", "+998907770001")

    n = 26                       # more than the default page of 20
    made = {await _issue(client, auth, cid, start=past) for _ in range(n)}

    first = await _worklist(client, auth)
    assert first["total"] == n
    assert len(first["items"]) == 20, "the default page stays bounded"

    # Walk the pages; the union must be exactly the contracts we created.
    seen: set[str] = set()
    offset = 0
    while offset < first["total"]:
        page = await _worklist(client, auth, limit=20, offset=offset)
        assert page["items"], "a page inside the total must not be empty"
        seen |= {i["id"] for i in page["items"]}
        offset += 20
    assert seen == made, "paging must reach every row exactly once"


async def test_pages_partition_rows_that_share_status_and_due_date(isolation_ctx) -> None:
    """The tie-breaker case: identical status AND identical due date."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    # One start date ⇒ one due date ⇒ every row ties on both sort keys.
    same = date.today() - timedelta(days=40)
    cid = await _customer(client, auth, "All Tied", "+998907770002")
    made = {await _issue(client, auth, cid, start=same) for _ in range(12)}

    pages = [await _worklist(client, auth, limit=4, offset=o) for o in (0, 4, 8)]
    ids = [i["id"] for p in pages for i in p["items"]]

    assert len(ids) == 12, "no row lost across pages"
    assert len(set(ids)) == 12, "no row served on two pages"
    assert set(ids) == made

    # And the order is reproducible: the same request twice gives the same page.
    again = await _worklist(client, auth, limit=4, offset=4)
    assert [i["id"] for i in again["items"]] == [i["id"] for i in pages[1]["items"]]


# ── date semantics ───────────────────────────────────────────────────────────

async def test_today_is_the_tenants_today_not_utc(isolation_ctx) -> None:
    """A contract due on the tenant's today counts, even when UTC disagrees.

    Asia/Tashkent is UTC+5, so for five hours each evening the tenant's calendar
    date is already tomorrow by UTC's reckoning. A contract due on the tenant's
    today must be in the queue throughout — this is the window the UTC version
    got wrong.
    """
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    tashkent_today = datetime.now(ZoneInfo("Asia/Tashkent")).date()
    utc_today = datetime.now(UTC).date()

    body = await _worklist(client, auth)
    assert body["timezone"] == "Asia/Tashkent", "the seeded tenant uses the column default"
    assert body["as_of_date"] == tashkent_today.isoformat()

    # Make the difference explicit when the run happens to straddle midnight UTC.
    if tashkent_today != utc_today:
        assert body["as_of_date"] != utc_today.isoformat(), \
            "the queue must classify by the tenant's day, not UTC's"

    # A contract whose first instalment falls on the tenant's today is included.
    # One CALENDAR month back, not 30 days: the schedule advances in months, so in
    # a 31-day month the literal offset puts the first instalment one day into the
    # future — not due today at all, which is a different test than this one.
    cid = await _customer(client, auth, "Due Today", "+998907770003")
    iid = await _issue(client, auth, cid, start=add_months(tashkent_today, -1))
    listed = {i["id"] for i in (await _worklist(client, auth, limit=200))["items"]}
    assert iid in listed


async def test_past_due_active_contract_counts_as_overdue_not_due_today(isolation_ctx) -> None:
    """The cron-lag case, and the reason status alone is not enough.

    `mark_overdue` runs nightly. Between a due date passing and that job running,
    the row is still `active`. Classifying by status would file it under "due
    today", understating the overdue figure the shop works from first.
    """
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    cid = await _customer(client, auth, "Cron Lag", "+998907770004")

    # Started long ago ⇒ next_due_date is in the past, but no cron has run in
    # this test database, so the row is still `active`.
    iid = await _issue(client, auth, cid, start=date.today() - timedelta(days=90))

    body = await _worklist(client, auth, limit=200)
    row = next(i for i in body["items"] if i["id"] == iid)
    assert row["status"] == "active", "precondition: the nightly job has not run"
    assert row["is_overdue"] is True, "a past due date is overdue regardless of the status column"
    assert body["overdue_count"] >= 1
    assert body["overdue_count"] + body["due_today_count"] == body["total"], \
        "the two buckets must partition the queue"


async def test_future_dated_contract_is_not_in_the_queue(isolation_ctx) -> None:
    """Nothing due yet is not work — the queue is due-or-earlier only."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    cid = await _customer(client, auth, "Not Yet", "+998907770005")
    iid = await _issue(client, auth, cid, start=date.today())   # first due ≈ +1 month

    body = await _worklist(client, auth, limit=200)
    assert iid not in {i["id"] for i in body["items"]}


async def test_overdue_sorts_before_due_today(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    cid = await _customer(client, auth, "Mixed", "+998907770006")
    await _issue(client, auth, cid, start=date.today() - timedelta(days=200))
    await _issue(client, auth, cid, start=date.today() - timedelta(days=30))

    items = (await _worklist(client, auth, limit=200))["items"]
    flags = [i["is_overdue"] for i in items]
    # Once a non-overdue row appears, no overdue row may follow it.
    assert flags == sorted(flags, key=lambda f: 0 if f else 1)


async def test_invalid_tenant_timezone_falls_back_without_failing(isolation_ctx) -> None:
    """A mistyped timezone column must degrade, never 500 the day's work."""
    from app.api.tenant import DEFAULT_TENANT_TZ, _tenant_today, _tenant_tz_name

    class _T:
        timezone = "Not/AZone"

    class _Blank:
        timezone = ""

    assert _tenant_tz_name(_T()) == DEFAULT_TENANT_TZ
    assert _tenant_tz_name(_Blank()) == DEFAULT_TENANT_TZ
    assert isinstance(_tenant_today(_T()), date)


async def test_worklist_is_tenant_scoped(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth_a = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    auth_b = {**ctx["auth_b"], "X-Tenant-Id": ctx["tenant_b"]}
    past = date.today() - timedelta(days=40)

    a = await _issue(client, auth_a, await _customer(client, auth_a, "Tenant A Buyer", "+998907770007"), start=past)
    b = await _issue(client, auth_b, await _customer(client, auth_b, "Tenant B Buyer", "+998907770008"), start=past)

    a_ids = {i["id"] for i in (await _worklist(client, auth_a, limit=200))["items"]}
    b_ids = {i["id"] for i in (await _worklist(client, auth_b, limit=200))["items"]}
    assert a in a_ids and b not in a_ids
    assert b in b_ids and a not in b_ids
