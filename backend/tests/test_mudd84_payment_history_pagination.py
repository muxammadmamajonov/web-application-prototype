"""MUDD-84 — payment history is a page presented as the whole answer.

`GET /tenant/payments` already accepted `status_filter` + `offset`/`limit`
correctly — the bug was entirely client-side: the frontend fetched only the
newest `limit=200` payments UNFILTERED, then filtered by status in the
browser. On a tenant with more than 200 payments, any rejected/approved/
refunded row older than the 200th was silently unreachable under any status
view, not merely slow to load.

These tests pin the SERVER side of the fix that makes real pagination
possible: a stable `id` tiebreaker (so OFFSET/LIMIT pages are a true
partition, matching the worklist's own contract — see
test_p3_worklist_completeness.py) and a `counts` breakdown that lets a
"Rad etilgan (N)" tab show the real tenant-wide figure regardless of which
page happens to be loaded.
"""
from __future__ import annotations


async def _make_customer(client, auth, name: str, phone: str) -> str:
    r = await client.post("/api/tenant/customers", headers=auth, json={"full_name": name, "phone": phone})
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    return r.json()["id"]


async def _issue(client, auth, cid: str) -> str:
    r = await client.post("/api/tenant/installments", headers=auth, json={
        "customer_id": cid, "months": 12,
        "items": [{"product_name_snapshot": "Phone", "unit_price": "60000000", "qty": 1}],
    })
    assert r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    return r.json()["id"]


async def test_a_rejected_payment_past_the_old_200_row_cap_is_still_fully_reachable(isolation_ctx) -> None:
    """The regression this ticket exists for: a rejected payment older than the
    frontend's old `limit=200`, status-UNFILTERED fetch was invisible under
    every status view. Proven at the layer that actually owns pagination:
    walking `status_filter=rejected` by offset must reach the WHOLE filtered
    set, not a slice of the newest 200 payments overall."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    cid = await _make_customer(client, auth, "History", "+998905550100")
    iid = await _issue(client, auth, cid)

    # 203 payments total; the OLDEST 3 (created first) are rejected — so under
    # newest-first ordering they sit past position 200, exactly where the old
    # client-side 200-cap fetch would have dropped them before any status
    # filter ever ran.
    rejected_ids: list[str] = []
    for i in range(203):
        rp = await client.post("/api/tenant/payments", headers=auth, json={
            "installment_id": iid, "amount": 1000, "method": "cash", "mark_approved": False,
        })
        assert rp.status_code == 201, f"{rp.status_code} {rp.text[:200]}"
        pid = rp.json()["id"]
        if i < 3:
            rr = await client.post(f"/api/tenant/payments/{pid}/reject", headers=auth)
            assert rr.status_code == 200, f"{rr.status_code} {rr.text[:200]}"
            rejected_ids.append(pid)

    # Walk every page of the "rejected" filter and collect everything found.
    found: set[str] = set()
    offset = 0
    total = None
    for _ in range(20):  # bounded — a real bug here must not hang the suite
        r = await client.get(
            f"/api/tenant/payments?status_filter=rejected&limit=50&offset={offset}", headers=auth,
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
        body = r.json()
        total = body["total"]
        found |= {p["id"] for p in body["items"]}
        offset += len(body["items"])
        if offset >= total or not body["items"]:
            break

    assert total == 3, f"expected 3 rejected payments, server reports {total}"
    assert found == set(rejected_ids), "every rejected payment must be reachable by paging the filter"


async def test_pagination_is_a_true_partition_with_no_duplicate_or_skipped_row(isolation_ctx) -> None:
    """Two payments can share a `created_at` timestamp when recorded in quick
    succession. Without a stable tiebreaker, OFFSET/LIMIT under that tie is
    unstable — a row can land on two pages or on neither, mirroring the exact
    property `test_worklist_is_bounded_and_pages_stably` pins for the
    collections queue."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    cid = await _make_customer(client, auth, "Pager", "+998905550101")
    iid = await _issue(client, auth, cid)
    for _ in range(12):
        rp = await client.post("/api/tenant/payments", headers=auth, json={
            "installment_id": iid, "amount": 1000, "method": "cash", "mark_approved": False,
        })
        assert rp.status_code == 201, f"{rp.status_code} {rp.text[:200]}"

    first = (await client.get("/api/tenant/payments?limit=5&offset=0", headers=auth)).json()
    second = (await client.get("/api/tenant/payments?limit=5&offset=5", headers=auth)).json()
    assert len(first["items"]) == 5
    assert len(second["items"]) == 5
    assert not ({p["id"] for p in first["items"]} & {p["id"] for p in second["items"]}), \
        "an unstable sort let the same payment land on two pages"
    assert first["total"] == second["total"] == 12


async def test_counts_reflect_the_whole_tenant_regardless_of_the_active_filter(isolation_ctx) -> None:
    """`counts` must describe the WHOLE tenant so a "Rad etilgan (N)" tab shows
    the real figure even while a different status is the active page — it must
    not be derived from whatever page happens to be loaded client-side."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    cid = await _make_customer(client, auth, "Counted", "+998905550102")
    iid = await _issue(client, auth, cid)
    pending_ids: list[str] = []
    for _ in range(4):
        rp = await client.post("/api/tenant/payments", headers=auth, json={
            "installment_id": iid, "amount": 1000, "method": "cash", "mark_approved": False,
        })
        assert rp.status_code == 201, f"{rp.status_code} {rp.text[:200]}"
        pending_ids.append(rp.json()["id"])
    for pid in pending_ids[:3]:
        rr = await client.post(f"/api/tenant/payments/{pid}/reject", headers=auth)
        assert rr.status_code == 200, f"{rr.status_code} {rr.text[:200]}"

    # Ask for the PENDING page — the counts on that response must still
    # report the rejected total correctly, not just what's on this page.
    r = await client.get("/api/tenant/payments?status_filter=pending", headers=auth)
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    counts = r.json()["counts"]
    assert counts == {"pending": 1, "approved": 0, "rejected": 3, "refunded": 0}
