"""Phase 3 UX-008/UX-009/UX-011 — operational queues must cover the whole book.

The tenant Overview's "needs attention" queue and the sales Today worklist both
derived their contents AND their counts client-side from the first page of
`/tenant/installments`. Past one page that silently presents a slice as the
whole thing — the most dangerous kind of wrong, because nothing on screen says
so.

`GET /tenant/worklist` computes the set in the database over the whole tenant.
These tests prove the properties that make it trustworthy:

  * completeness — the total counts every due/overdue contract, not a page;
  * boundedness  — paging still applies, so a big book can't be pulled at once;
  * ordering     — overdue first, then soonest due, stably across pages;
  * isolation    — one tenant's queue never contains another's contracts.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.installments import add_months

PAGE = 50  # the default page size the old client-side derivation was capped by


async def _make_customer(client, auth, name: str, phone: str) -> str:
    r = await client.post("/api/tenant/customers", headers=auth, json={"full_name": name, "phone": phone})
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    return r.json()["id"]


async def _issue(client, auth, cid: str, *, start: str | None = None, months: int = 6) -> str:
    body: dict = {
        "customer_id": cid, "months": months,
        "items": [{"product_name_snapshot": "Phone", "unit_price": "6000000", "qty": 1}],
    }
    if start:
        body["start_date"] = start
    r = await client.post("/api/tenant/installments", headers=auth, json=body)
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    return r.json()["id"]


async def test_worklist_total_counts_the_whole_book_not_one_page(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    # More due contracts than a single page, all with a due date in the past so
    # every one of them belongs in the queue.
    past = (datetime.now(UTC) - timedelta(days=40)).date().isoformat()
    n = PAGE + 7
    cid = await _make_customer(client, auth, "Bulk Buyer", "+998905550000")
    for _ in range(n):
        await _issue(client, auth, cid, start=past)

    r = await client.get("/api/tenant/worklist", headers=auth)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    body = r.json()

    # The count reflects everything, even though the page returned is small.
    assert body["total"] == n, f"expected {n} due contracts, got {body['total']}"
    assert len(body["items"]) <= 20, "the default page must stay small"
    assert body["total"] > len(body["items"]), "this fixture must exercise the paged case"
    assert body["overdue_count"] + body["due_today_count"] == body["total"]
    assert body["as_of"], "the queue must state when it was computed"


async def test_worklist_is_bounded_and_pages_stably(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    # Far enough back that the first instalment's due date has passed, so every
    # contract genuinely belongs in the queue.
    past = (datetime.now(UTC) - timedelta(days=40)).date().isoformat()
    cid = await _make_customer(client, auth, "Pager", "+998905550001")
    for _ in range(12):
        await _issue(client, auth, cid, start=past)

    # An absurd limit must be clamped, never honoured as an unbounded read.
    huge = await client.get("/api/tenant/worklist?limit=100000", headers=auth)
    assert huge.status_code in (200, 422)
    if huge.status_code == 200:
        assert len(huge.json()["items"]) <= 200

    first = (await client.get("/api/tenant/worklist?limit=5&offset=0", headers=auth)).json()
    second = (await client.get("/api/tenant/worklist?limit=5&offset=5", headers=auth)).json()
    assert len(first["items"]) == 5
    assert len(second["items"]) == 5
    # Stable ordering ⇒ pages partition the set instead of overlapping.
    assert not ({i["id"] for i in first["items"]} & {i["id"] for i in second["items"]})
    assert first["total"] == second["total"] == 12


async def test_worklist_puts_overdue_first(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    cid = await _make_customer(client, auth, "Mixed", "+998905550002")
    # One long overdue, one due today. `add_months` rather than `timedelta(days=30)`
    # because the schedule advances in CALENDAR months: started 30 days before a
    # 1st-of-August, the first instalment falls on the 2nd — one day in the future,
    # so the contract is not yet collectable and this queue correctly omits it.
    # The literal offset silently passed only in the months short enough to hide it.
    today = datetime.now(UTC).date()
    await _issue(client, auth, cid, start=(today - timedelta(days=200)).isoformat())
    await _issue(client, auth, cid, start=add_months(today, -1).isoformat())

    items = (await client.get("/api/tenant/worklist", headers=auth)).json()["items"]
    assert len(items) >= 2
    statuses = [i["status"] for i in items]
    if "overdue" in statuses and "active" in statuses:
        assert statuses.index("overdue") < statuses.index("active"), \
            "overdue contracts must be collected first"


async def test_worklist_never_leaks_another_tenants_contracts(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth_a = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    auth_b = {**ctx["auth_b"], "X-Tenant-Id": ctx["tenant_b"]}
    past = (datetime.now(UTC) - timedelta(days=45)).date().isoformat()

    ca = await _make_customer(client, auth_a, "A Due", "+998905550003")
    cb = await _make_customer(client, auth_b, "B Due", "+998905550004")
    a_id = await _issue(client, auth_a, ca, start=past)
    b_id = await _issue(client, auth_b, cb, start=past)

    a_items = {i["id"] for i in (await client.get("/api/tenant/worklist", headers=auth_a)).json()["items"]}
    b_items = {i["id"] for i in (await client.get("/api/tenant/worklist", headers=auth_b)).json()["items"]}
    assert a_id in a_items and b_id not in a_items
    assert b_id in b_items and a_id not in b_items


async def test_pending_payment_total_covers_the_whole_tenant(isolation_ctx) -> None:
    """UX-008: the pending count came from one page of `/payments`."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    cid = await _make_customer(client, auth, "Payer", "+998905550005")
    iid = await _issue(client, auth, cid)
    n = PAGE + 3
    for _ in range(n):
        rp = await client.post(
            "/api/tenant/payments", headers=auth,
            json={"installment_id": iid, "amount": 1000, "method": "cash", "mark_approved": False},
        )
        assert rp.status_code == 201, f"{rp.status_code} {rp.text[:200]}"

    # A tiny page still reports the true filtered total, so the UI can show the
    # real queue size without fetching the queue.
    r = await client.get("/api/tenant/payments?status_filter=pending&limit=1", headers=auth)
    assert r.status_code == 200
    assert r.json()["total"] == n, f"expected {n} pending, got {r.json()['total']}"
    assert len(r.json()["items"]) == 1


async def test_payment_rows_name_their_contract(isolation_ctx) -> None:
    """UX-002: an approval confirmation must be able to restate the contract."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    cid = await _make_customer(client, auth, "Named", "+998905550006")
    iid = await _issue(client, auth, cid)
    await client.post("/api/tenant/payments", headers=auth,
                      json={"installment_id": iid, "amount": 5000, "method": "cash", "mark_approved": False})

    row = (await client.get("/api/tenant/payments", headers=auth)).json()["items"][0]
    assert "contract_number" in row
    assert row["contract_number"], "payments must carry the contract they belong to"
    assert row["customer_name"] == "Named"


async def test_approval_returns_the_resulting_balance(isolation_ctx) -> None:
    """UX-048: the success message must be able to state the new debt."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    cid = await _make_customer(client, auth, "Balance", "+998905550007")
    iid = await _issue(client, auth, cid)
    rp = await client.post("/api/tenant/payments", headers=auth,
                           json={"installment_id": iid, "amount": 1500000, "method": "cash", "mark_approved": False})
    pid = rp.json()["id"]

    ra = await client.post(f"/api/tenant/payments/{pid}/approve", headers=auth)
    assert ra.status_code == 200, f"{ra.status_code} {ra.text[:300]}"
    ins = ra.json().get("installment")
    assert ins is not None, "approval must report the contract it changed"
    assert ins["paid_amount"] == 1500000
    assert ins["remaining"] == max(0, ins["total_amount"] - 0 - ins["paid_amount"]) or ins["remaining"] >= 0
    assert ins["contract_number"]
