"""C3-03 — the collections worklist must be searchable server-side.

The tenant UI shows one search box above the collections view, but the view's
rows come from ``/tenant/worklist`` while the search term was being sent to
``/tenant/installments``. Typing filtered a list nobody was looking at, so the
box appeared to do nothing.

Search therefore has to exist HERE, over the tenant's whole due/overdue set —
filtering the page already fetched would search a fraction of the queue and
report it as the whole answer, which is the same defect the worklist endpoint
was created to fix.

These tests pin: the search vocabulary, that counts describe the filtered set,
that classification/ordering/paging survive a filter, and that search cannot
reach another tenant's data.
"""
from __future__ import annotations

from datetime import date, timedelta


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


# ── the search vocabulary ────────────────────────────────────────────────────

async def test_search_matches_customer_name(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    overdue = date.today() - timedelta(days=40)

    aziz = await _customer(client, auth, "Aziz Karimov", "+998901110001")
    bek = await _customer(client, auth, "Bekzod Tursunov", "+998901110002")
    aziz_c = await _issue(client, auth, aziz, start=overdue)
    await _issue(client, auth, bek, start=overdue)

    all_rows = await _worklist(client, auth)
    assert all_rows["total"] == 2

    hit = await _worklist(client, auth, q="Aziz")
    assert hit["total"] == 1, "the count must describe the FILTERED set"
    assert [i["id"] for i in hit["items"]] == [aziz_c]


async def test_search_matches_customer_phone(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    overdue = date.today() - timedelta(days=40)
    a = await _customer(client, auth, "Phone A", "+998901230077")
    b = await _customer(client, auth, "Phone B", "+998907650088")
    ca = await _issue(client, auth, a, start=overdue)
    await _issue(client, auth, b, start=overdue)

    hit = await _worklist(client, auth, q="1230077")
    assert [i["id"] for i in hit["items"]] == [ca]
    assert hit["total"] == 1


async def test_search_matches_contract_number(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    overdue = date.today() - timedelta(days=40)
    cid = await _customer(client, auth, "Contract Search", "+998901110009")
    first = await _issue(client, auth, cid, start=overdue)
    await _issue(client, auth, cid, start=overdue)

    rows = {i["id"]: i for i in (await _worklist(client, auth))["items"]}
    number = rows[first]["contract_number"]
    assert number, "a contract number is needed for this test to mean anything"

    hit = await _worklist(client, auth, q=number)
    assert [i["id"] for i in hit["items"]] == [first]
    assert hit["total"] == 1


async def test_search_is_case_insensitive_and_partial(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    overdue = date.today() - timedelta(days=40)
    cid = await _customer(client, auth, "Dilnoza Rahimova", "+998901110010")
    made = await _issue(client, auth, cid, start=overdue)

    for term in ("dilnoza", "DILNOZA", "lnoz", "Rahim"):
        hit = await _worklist(client, auth, q=term)
        assert [i["id"] for i in hit["items"]] == [made], f"term {term!r} found nothing"


# ── search does not break the queue's other guarantees ───────────────────────

async def test_search_spans_the_whole_set_not_just_the_first_page(isolation_ctx) -> None:
    """The point of doing this server-side: a match on page 3 is still found."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    overdue = date.today() - timedelta(days=40)

    # The filler is deeply overdue so it sorts first; the needle is only just
    # overdue, so it lands at the END of the queue — past the first page.
    bulk = await _customer(client, auth, "Bulk Filler", "+998902220001")
    for _ in range(25):                      # more than one page of noise
        await _issue(client, auth, bulk, start=date.today() - timedelta(days=200))
    needle_c = await _customer(client, auth, "Zuhra Needle", "+998902220002")
    needle = await _issue(client, auth, needle_c, start=overdue)

    # The needle is NOT on the default first page of the unfiltered queue…
    first_page = await _worklist(client, auth, limit=20, offset=0)
    assert needle not in {i["id"] for i in first_page["items"]}

    # …yet a search finds it, because the filter runs over the whole set.
    hit = await _worklist(client, auth, q="Zuhra")
    assert [i["id"] for i in hit["items"]] == [needle]
    assert hit["total"] == 1


async def test_search_preserves_classification_and_does_not_widen_the_set(isolation_ctx) -> None:
    """A filter may only narrow. It must not drag in rows the queue excludes."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    cid = await _customer(client, auth, "Split Semantics", "+998903330001")
    late = await _issue(client, auth, cid, start=date.today() - timedelta(days=40))
    # Same customer, so it matches the term — but nothing is due on it yet, so
    # the queue must still leave it out.
    not_due_yet = await _issue(client, auth, cid, start=date.today())

    hit = await _worklist(client, auth, q="Split")
    ids = {i["id"] for i in hit["items"]}
    assert ids == {late}, "search must not pull in a contract with nothing due"
    assert not_due_yet not in ids
    assert hit["total"] == 1
    # The buckets must still be exact complements of the FILTERED total.
    assert hit["overdue_count"] + hit["due_today_count"] == hit["total"]
    assert hit["items"][0]["is_overdue"] is True
    # And the tenant-local as-of metadata still comes back.
    assert hit["as_of_date"] and hit["timezone"]


async def test_search_keeps_overdue_first_ordering(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    cid = await _customer(client, auth, "Order Check", "+998903330002")
    recent = await _issue(client, auth, cid, start=date.today() - timedelta(days=31))
    ancient = await _issue(client, auth, cid, start=date.today() - timedelta(days=300))

    items = (await _worklist(client, auth, q="Order"))["items"]
    ids = [i["id"] for i in items]
    assert ids == [ancient, recent], "soonest-due-first ordering must survive a filter"
    flags = [i["is_overdue"] for i in items]
    # Once a non-overdue row appears, no overdue row may follow it.
    assert flags == sorted(flags, key=lambda f: 0 if f else 1)


async def test_search_pages_deterministically(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    overdue = date.today() - timedelta(days=40)
    cid = await _customer(client, auth, "Paged Match", "+998903330003")
    made = {await _issue(client, auth, cid, start=overdue) for _ in range(25)}

    seen: list[str] = []
    offset = 0
    while True:
        page = await _worklist(client, auth, q="Paged", limit=10, offset=offset)
        assert page["total"] == 25
        if not page["items"]:
            break
        seen.extend(i["id"] for i in page["items"])
        offset += len(page["items"])

    assert len(seen) == len(set(seen)), "a row appeared on two pages"
    assert set(seen) == made, "paging over a filtered set lost or invented rows"


# ── search is still tenant-scoped ────────────────────────────────────────────

async def test_search_cannot_reach_another_tenants_rows(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth_a = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    auth_b = {**ctx["auth_b"], "X-Tenant-Id": ctx["tenant_b"]}
    overdue = date.today() - timedelta(days=40)

    shared_name = "Sharedname Customer"
    a_cust = await _customer(client, auth_a, shared_name, "+998904440001")
    b_cust = await _customer(client, auth_b, shared_name, "+998904440002")
    a_contract = await _issue(client, auth_a, a_cust, start=overdue)
    b_contract = await _issue(client, auth_b, b_cust, start=overdue)

    a_hit = await _worklist(client, auth_a, q="Sharedname")
    assert [i["id"] for i in a_hit["items"]] == [a_contract]
    assert b_contract not in {i["id"] for i in a_hit["items"]}
    assert a_hit["total"] == 1

    b_hit = await _worklist(client, auth_b, q="Sharedname")
    assert [i["id"] for i in b_hit["items"]] == [b_contract]
    assert b_hit["total"] == 1


# ── degenerate inputs ────────────────────────────────────────────────────────

async def test_blank_search_returns_the_whole_queue(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    overdue = date.today() - timedelta(days=40)
    cid = await _customer(client, auth, "Blank Term", "+998905550001")
    await _issue(client, auth, cid, start=overdue)
    await _issue(client, auth, cid, start=overdue)

    full = (await _worklist(client, auth))["total"]
    for blank in ("", "   "):
        assert (await _worklist(client, auth, q=blank))["total"] == full, (
            "a blank term must clear the filter, not match nothing"
        )


async def test_search_with_no_matches_is_an_honest_empty_set(isolation_ctx) -> None:
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    overdue = date.today() - timedelta(days=40)
    cid = await _customer(client, auth, "Real Person", "+998905550002")
    await _issue(client, auth, cid, start=overdue)

    miss = await _worklist(client, auth, q="nobody-by-this-name")
    assert miss["items"] == []
    assert miss["total"] == 0
    assert miss["overdue_count"] == 0 and miss["due_today_count"] == 0


# ── the search term is bounded (F4-06) ───────────────────────────────────────

async def test_search_accepts_a_term_at_the_maximum_length(isolation_ctx) -> None:
    """120 characters is the documented bound and must still be served."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    r = await client.get("/api/tenant/worklist", headers=auth, params={"q": "x" * 120})
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    assert r.json()["total"] == 0          # nothing matches, but it is a valid query


async def test_search_rejects_an_oversized_term(isolation_ctx) -> None:
    """One character past the bound is refused, not silently truncated."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    r = await client.get("/api/tenant/worklist", headers=auth, params={"q": "x" * 121})
    assert r.status_code == 422, f"expected a validation refusal, got {r.status_code}"


async def test_a_bounded_term_still_filters_normally(isolation_ctx) -> None:
    """The cap must not disturb ordinary search."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    overdue = date.today() - timedelta(days=40)
    cid = await _customer(client, auth, "Bounded Term", "+998906660001")
    made = await _issue(client, auth, cid, start=overdue)
    hit = await _worklist(client, auth, q="Bounded")
    assert [i["id"] for i in hit["items"]] == [made]


async def test_the_installments_list_shares_the_same_bound(isolation_ctx) -> None:
    """The worklist mirrors `/installments`' search, so the two agree on limits."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    ok = await client.get("/api/tenant/installments", headers=auth, params={"q": "x" * 120})
    assert ok.status_code == 200
    too_long = await client.get("/api/tenant/installments", headers=auth, params={"q": "x" * 121})
    assert too_long.status_code == 422


async def test_the_bound_does_not_leak_tenant_scoping(isolation_ctx) -> None:
    """A refused or maximal term changes nothing about isolation."""
    client, ctx = isolation_ctx
    auth_a = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    auth_b = {**ctx["auth_b"], "X-Tenant-Id": ctx["tenant_b"]}
    overdue = date.today() - timedelta(days=40)
    b_cust = await _customer(client, auth_b, "Edge Case Person", "+998906660002")
    await _issue(client, auth_b, b_cust, start=overdue)

    padded = "Edge Case Person".ljust(120, ".")
    a_hit = await _worklist(client, auth_a, q=padded[:120])
    assert a_hit["items"] == [] and a_hit["total"] == 0
