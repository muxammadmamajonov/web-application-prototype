"""MUDD-83 over the wire: a contract line records WHAT it sold, and the ledger
can be read by that.

`reports.products()` already grouped by category, but it read one through
`Product.category` on an OUTER JOIN — so every line written from the global
catalog or as free text (`product_id IS NULL`) fell into a single `"—"` bucket.
For a shop that sells mostly things it does not stock, that is most of the
book. The snapshot column closes that gap; these tests hold the behaviour that
makes it worth having.

What only a real request and a real database can show, and what is therefore
here rather than in a unit test: that the server derives the category when a
client does not send one, that a client CANNOT invent a slug the catalog does
not know, that filtering by category returns a multi-line contract exactly
once, and that the chip counts do not move when a different filter is applied.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

INSTALLMENTS = "/api/tenant/installments"
OVERVIEW = "/api/tenant/overview"


@pytest.fixture
def _no_telegram(monkeypatch):
    async def _noop(*_a, **_kw):
        return None

    from app.services import customer_outreach
    monkeypatch.setattr(customer_outreach, "notify_new_contract", _noop)


@pytest.fixture(autouse=True)
def _no_overview_cache(monkeypatch):
    """The overview is served from a 30s Redis cache keyed per tenant.

    Two tests in this file read it after writing different contracts for the
    SAME tenant, so a cached answer would make the second one assert against
    the first one's numbers — passing or failing for a reason that has nothing
    to do with the code under test.
    """
    from app.core import cache

    async def _straight_through(_key, *, ttl, compute, label=None):
        return await compute()

    # The endpoint imports it inside the function body, so patching it on the
    # module is enough — there is no module-level alias to also replace.
    monkeypatch.setattr(cache, "cached_json", _straight_through)


@pytest_asyncio.fixture
async def ctx(_no_telegram):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import (
        CatalogBrand,
        CatalogCategory,
        CatalogModel,
        Customer,
        Product,
        Tenant,
        TenantSettings,
        TenantUser,
        User,
    )
    from app.models.enums import CustomerStatus, TenantRole

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            t = Tenant(slug="cat-shop", name="Cat Shop")
            s.add(t); await s.flush()
            s.add(TenantSettings(tenant_id=t.id, currency="UZS"))
            owner = User(telegram_id=880001)
            s.add(owner); await s.flush()
            s.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
            c = Customer(tenant_id=t.id, full_name="Buyer", phone="+998901234601",
                         status=CustomerStatus.active)
            s.add(c); await s.flush()

            # Two live categories and one retired one. `avtomobillar` is
            # deliberately outside a phone shop's segment: the whole point of
            # the change is that the segment no longer gates what can be sold.
            phones = CatalogCategory(slug="smartfonlar", name_uz="Smartfonlar",
                                     name_ru="Смартфоны", business_types=["electronics"])
            cars = CatalogCategory(slug="avtomobillar", name_uz="Avtomobillar",
                                   name_ru="Автомобили", business_types=["auto"])
            retired = CatalogCategory(slug="pereferiya", name_uz="Periferiya",
                                      name_ru="Периферия", business_types=[], is_active=False)
            s.add_all([phones, cars, retired]); await s.flush()

            brand = CatalogBrand(slug="apple", name="Apple", category_id=phones.id,
                                 category_ids=[str(phones.id)])
            s.add(brand); await s.flush()
            # The model carries its OWN category — that is the edge the resolver
            # follows. A brand can span categories, so the brand hop would lie.
            model = CatalogModel(brand_id=brand.id, category_id=phones.id,
                                 slug="iphone-15", name="iPhone 15")
            s.add(model); await s.flush()

            stocked = Product(tenant_id=t.id, name_uz="Sovutgich", category="avtomobillar",
                              base_price=1, is_active=True)
            # `products.category` is free text and predates the catalog, so it
            # can hold a slug no category has. That is the case the resolver
            # has to absorb rather than reject.
            legacy = Product(tenant_id=t.id, name_uz="Eski mahsulot",
                             category="a-slug-that-no-longer-exists",
                             base_price=1, is_active=True)
            s.add_all([stocked, legacy]); await s.flush()

            data = {
                "tenant_id": str(t.id), "customer_id": str(c.id), "session": Session,
                "catalog_model_id": str(model.id),
                "stocked_product_id": str(stocked.id),
                "legacy_product_id": str(legacy.id),
                "auth": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
            }
            await s.commit()
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, data
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _body(d, *items: dict) -> dict:
    return {
        "customer_id": d["customer_id"], "months": 6, "down_payment": "0",
        "items": [{"product_name_snapshot": "Mahsulot", "qty": 1,
                   "unit_price": "1000000", **it} for it in items],
    }


async def _create(client, d, *items: dict):
    return await client.post(INSTALLMENTS, headers=d["auth"], json=_body(d, *items))


async def _items_of(client, d, contract_id) -> list[dict]:
    r = await client.get(f"{INSTALLMENTS}/{contract_id}", headers=d["auth"])
    return r.json()["items"]


# ── how a line gets its category ────────────────────────────────────────────

async def test_the_category_the_user_picked_is_what_gets_stored(ctx) -> None:
    """The one-step picker is the whole feature: choose a kind, type a name."""
    client, d = ctx
    r = await _create(client, d, {"category": "avtomobillar",
                                  "product_name_snapshot": "Nexia 3"})
    assert r.status_code in (200, 201), r.text
    assert (await _items_of(client, d, r.json()["id"]))[0]["category"] == "avtomobillar"


async def test_an_electronics_shop_can_sell_a_car(ctx) -> None:
    """The reported bug, stated as a test.

    The tenant's `business_type` used to gate the category list, so a phone
    shop could never file a sale under `avtomobillar` — and a mixed shop was
    simply stuck. Nothing about the write path may reintroduce that gate.
    """
    client, d = ctx
    r = await _create(client, d, {"category": "avtomobillar", "product_name_snapshot": "Malibu"})
    assert r.status_code in (200, 201), r.text

    listed = (await client.get(INSTALLMENTS, headers=d["auth"],
                               params={"category": "avtomobillar"})).json()
    assert [i["id"] for i in listed["items"]] == [r.json()["id"]]


async def test_a_catalog_line_is_categorised_without_the_client_saying_so(ctx) -> None:
    """Existing clients keep working AND start producing categorised rows.

    Derived from the MODEL's category, never from the brand's: a brand spans
    several categories now, so the brand hop would file this shop's laptop
    under `smartfonlar`.
    """
    client, d = ctx
    r = await _create(client, d, {"catalog_model_id": d["catalog_model_id"],
                                  "product_name_snapshot": "Apple iPhone 15"})
    assert r.status_code in (200, 201), r.text
    assert (await _items_of(client, d, r.json()["id"]))[0]["category"] == "smartfonlar"


async def test_a_catalog_derived_category_survives_the_category_being_retired(ctx) -> None:
    """(MUDD-92 M1) The API and the `rj0itemcategory` backfill must agree on
    what a retired category means for a row that never went through the
    client at all — a `catalog_model_id` line derives its category from the
    model's own row, and that derivation has to give the same answer whether
    the category was active when the line was written. The migration's
    backfill already never checked `is_active` for this edge (see its
    docstring); this pins the API side to the same answer.
    """
    client, d = ctx

    from sqlalchemy import update

    from app.models import CatalogCategory

    async with d["session"]() as s:
        await s.execute(
            update(CatalogCategory).where(CatalogCategory.slug == "smartfonlar")
            .values(is_active=False)
        )
        await s.commit()

    r = await _create(client, d, {"catalog_model_id": d["catalog_model_id"],
                                  "product_name_snapshot": "Apple iPhone 15"})
    assert r.status_code in (200, 201), r.text
    assert (await _items_of(client, d, r.json()["id"]))[0]["category"] == "smartfonlar"


async def test_a_stocked_line_falls_back_to_the_products_own_category(ctx) -> None:
    client, d = ctx
    r = await _create(client, d, {"product_id": d["stocked_product_id"],
                                  "product_name_snapshot": "Sovutgich"})
    assert r.status_code in (200, 201), r.text
    assert (await _items_of(client, d, r.json()["id"]))[0]["category"] == "avtomobillar"


async def test_what_the_user_picked_beats_what_the_product_row_says(ctx) -> None:
    """A product row can be stale; the person at the counter is looking at it."""
    client, d = ctx
    r = await _create(client, d, {"product_id": d["stocked_product_id"],
                                  "category": "smartfonlar",
                                  "product_name_snapshot": "Sovutgich"})
    assert r.status_code in (200, 201), r.text
    assert (await _items_of(client, d, r.json()["id"]))[0]["category"] == "smartfonlar"


async def test_a_free_text_line_stays_uncategorised_rather_than_guessing(ctx) -> None:
    """NULL is an answer. Inventing a category puts fiction in a revenue chart."""
    client, d = ctx
    r = await _create(client, d, {"product_name_snapshot": "Nomsiz narsa"})
    assert r.status_code in (200, 201), r.text
    assert (await _items_of(client, d, r.json()["id"]))[0]["category"] is None


# ── what the API refuses, and what it forgives ──────────────────────────────

async def test_a_slug_the_catalog_does_not_know_is_refused(ctx) -> None:
    """Otherwise the UI shows a category the filter can never select.

    Loud, not silent: the client sent it, so the client has the bug.
    """
    client, d = ctx
    r = await _create(client, d, {"category": "not-a-real-category",
                                  "product_name_snapshot": "X"})
    assert r.status_code == 400, r.text
    assert "unknown_category" in r.json()["detail"]


async def test_a_retired_category_is_accepted_not_treated_as_invented(ctx) -> None:
    """(MUDD-92 H1) `is_active=False` means "not offered for a NEW pick", not
    "no longer a real category". The resolver cannot tell a fresh pick from an
    unchanged value on an edited line — both arrive as the same `category`
    field — so refusing one would refuse the other. Existence, not
    `is_active`, is what makes a slug legitimate.
    """
    client, d = ctx
    r = await _create(client, d, {"category": "pereferiya", "product_name_snapshot": "Sichqoncha"})
    assert r.status_code in (200, 201), r.text
    assert (await _items_of(client, d, r.json()["id"]))[0]["category"] == "pereferiya"


async def test_a_stale_slug_on_our_own_product_row_does_not_block_the_sale(ctx) -> None:
    """`products.category` is free text and predates the catalog.

    Refusing the sale would punish the tenant for a row they cannot see, so the
    derived value is dropped to NULL instead. The asymmetry with the test above
    is deliberate: their mistake is loud, ours is absorbed.
    """
    client, d = ctx
    r = await _create(client, d, {"product_id": d["legacy_product_id"],
                                  "product_name_snapshot": "Eski mahsulot"})
    assert r.status_code in (200, 201), r.text
    assert (await _items_of(client, d, r.json()["id"]))[0]["category"] is None


async def test_editing_a_contract_recategorises_it(ctx) -> None:
    """PUT rewrites the items, so it must resolve categories the same way."""
    client, d = ctx
    created = await _create(client, d, {"category": "smartfonlar", "product_name_snapshot": "A"})
    contract_id = created.json()["id"]

    r = await client.put(
        f"{INSTALLMENTS}/{contract_id}", headers=d["auth"],
        json=_body(d, {"product_name_snapshot": "Nexia", "category": "avtomobillar"}),
    )
    assert r.status_code in (200, 204), r.text
    assert (await _items_of(client, d, contract_id))[0]["category"] == "avtomobillar"


async def test_editing_a_contract_survives_its_own_category_being_retired(ctx) -> None:
    """(MUDD-92 H1) The bug, exactly as it happens.

    The edit form prefills every line from what is already stored (see
    `InstallmentModal.tsx`'s prefill effect), then PUTs the whole item list
    back — the retired line's OWN unchanged category rides along as an
    ordinary `category` field. Before this fix that made `sent` (client-chosen)
    disagree with `live` (`is_active` categories only), and ANY edit to a
    contract holding a since-retired category — changing the phone number,
    fixing the amount, anything — hit `400 unknown_category`. There was no way
    out: the retired slug is also excluded from the picker's own option list,
    so the field could not even be cleared to something else.
    """
    client, d = ctx
    created = await _create(client, d, {"category": "smartfonlar", "product_name_snapshot": "iPhone"})
    contract_id = created.json()["id"]

    from sqlalchemy import update

    from app.models import CatalogCategory

    async with d["session"]() as s:
        await s.execute(
            update(CatalogCategory).where(CatalogCategory.slug == "smartfonlar")
            .values(is_active=False)
        )
        await s.commit()

    r = await client.put(
        f"{INSTALLMENTS}/{contract_id}", headers=d["auth"],
        json=_body(d, {"product_name_snapshot": "iPhone", "category": "smartfonlar"}),
    )
    assert r.status_code in (200, 204), r.text
    assert (await _items_of(client, d, contract_id))[0]["category"] == "smartfonlar"


# ── reading the ledger by category ──────────────────────────────────────────

async def test_a_contract_with_two_lines_of_one_category_is_listed_once(ctx) -> None:
    """The reason the filter is an EXISTS and not a JOIN.

    A join would return this contract twice, and then `total`, the page size
    and the offset would all disagree with each other.
    """
    client, d = ctx
    r = await _create(client, d,
                      {"category": "smartfonlar", "product_name_snapshot": "iPhone"},
                      {"category": "smartfonlar", "product_name_snapshot": "Samsung"})
    assert r.status_code in (200, 201), r.text

    listed = (await client.get(INSTALLMENTS, headers=d["auth"],
                               params={"category": "smartfonlar"})).json()
    assert [i["id"] for i in listed["items"]] == [r.json()["id"]]
    assert listed["total"] == 1


async def test_a_mixed_contract_is_found_under_either_category(ctx) -> None:
    """One contract can sell a phone and a car. Both filters should find it."""
    client, d = ctx
    r = await _create(client, d,
                      {"category": "smartfonlar", "product_name_snapshot": "iPhone"},
                      {"category": "avtomobillar", "product_name_snapshot": "Nexia"})
    contract_id = r.json()["id"]

    for slug in ("smartfonlar", "avtomobillar"):
        listed = (await client.get(INSTALLMENTS, headers=d["auth"],
                                   params={"category": slug})).json()
        assert [i["id"] for i in listed["items"]] == [contract_id], slug


async def test_the_category_chips_count_contracts_not_lines(ctx) -> None:
    """A chip saying 2 must reveal 2 rows when it is clicked."""
    client, d = ctx
    await _create(client, d,
                  {"category": "smartfonlar", "product_name_snapshot": "iPhone"},
                  {"category": "smartfonlar", "product_name_snapshot": "Samsung"})
    await _create(client, d, {"category": "avtomobillar", "product_name_snapshot": "Nexia"})

    counts = (await client.get(INSTALLMENTS, headers=d["auth"])).json()["category_counts"]
    assert counts == {"smartfonlar": 1, "avtomobillar": 1}


async def test_the_chip_counts_do_not_move_when_another_filter_is_applied(ctx) -> None:
    """A chip has to show how many rows it WOULD reveal.

    Counting from the already-filtered set makes every other chip read zero the
    moment one filter is on, which is exactly when a user needs to see where
    else to look.
    """
    client, d = ctx
    await _create(client, d, {"category": "smartfonlar", "product_name_snapshot": "iPhone"})
    await _create(client, d, {"category": "avtomobillar", "product_name_snapshot": "Nexia"})

    unfiltered = (await client.get(INSTALLMENTS, headers=d["auth"])).json()
    filtered = (await client.get(INSTALLMENTS, headers=d["auth"],
                                 params={"category": "smartfonlar"})).json()

    assert filtered["total"] == 1
    assert filtered["category_counts"] == unfiltered["category_counts"]
    assert filtered["category_counts"] == {"smartfonlar": 1, "avtomobillar": 1}


async def test_uncategorised_lines_are_absent_from_the_breakdown_not_zero(ctx) -> None:
    """A `null` bucket in a chip row is a control nobody can act on."""
    client, d = ctx
    await _create(client, d, {"product_name_snapshot": "Nomsiz"})
    assert (await client.get(INSTALLMENTS, headers=d["auth"])).json()["category_counts"] == {}


# ── the dashboard ───────────────────────────────────────────────────────────

async def test_the_overview_reports_money_per_category_biggest_first(ctx) -> None:
    """"23 phone contracts" and "phones are 61% of the book" differ.

    A shop deciding what to stock needs the second one, so the breakdown
    carries both, ordered by value.
    """
    client, d = ctx
    await _create(client, d, {"category": "smartfonlar", "product_name_snapshot": "iPhone",
                              "unit_price": "2000000"})
    await _create(client, d, {"category": "avtomobillar", "product_name_snapshot": "Nexia",
                              "unit_price": "90000000"})

    by_category = (await client.get(OVERVIEW, headers=d["auth"])).json()["installments_by_category"]
    assert [row["category"] for row in by_category] == ["avtomobillar", "smartfonlar"]
    assert by_category[0]["amount_uzs"] == 90_000_000
    assert by_category[0]["count"] == 1


async def test_a_mixed_contract_splits_its_money_across_its_lines(ctx) -> None:
    """Attributing the whole contract to either category double-counts it."""
    client, d = ctx
    await _create(client, d,
                  {"category": "smartfonlar", "product_name_snapshot": "iPhone",
                   "unit_price": "3000000"},
                  {"category": "avtomobillar", "product_name_snapshot": "Nexia",
                   "unit_price": "7000000"})

    by_category = (await client.get(OVERVIEW, headers=d["auth"])).json()["installments_by_category"]
    amounts = {row["category"]: row["amount_uzs"] for row in by_category}
    assert amounts == {"smartfonlar": 3_000_000, "avtomobillar": 7_000_000}


async def test_a_cancelled_contract_is_not_a_sale(ctx) -> None:
    """`reports.products()` draws the same line; a breakdown that disagreed
    with the report would make both untrustworthy."""
    client, d = ctx
    created = await _create(client, d, {"category": "smartfonlar",
                                        "product_name_snapshot": "iPhone"})
    cancelled = await client.post(f"{INSTALLMENTS}/{created.json()['id']}/cancel",
                                  headers=d["auth"], json={"reason": "mijoz voz kechdi"})
    assert cancelled.status_code in (200, 204), cancelled.text

    by_category = (await client.get(OVERVIEW, headers=d["auth"])).json()["installments_by_category"]
    assert by_category == []
