"""The rj0itemcategory backfill, against rows that actually exist.

Running the migration on an empty database proves the SQL parses. It proves
nothing about what the backfill DECIDES, and the decisions are the whole point:
which edge it follows to find a category, which one it refuses to follow, and
what it does with a row it cannot resolve.

So this seeds a ledger that already contains every case, downgrades the column
away, and upgrades again — the same path a production deploy takes.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio

REVISION = "rj0itemcategory"
PREVIOUS = "rh1paytenantidx"


def _alembic(target: str) -> None:
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), target) if target != PREVIOUS else \
        command.downgrade(Config("alembic.ini"), target)


async def _seed(engine) -> dict[str, str]:
    """A tenant, a customer, a contract, and one line of every provenance.

    Seeded through the ORM rather than raw INSERTs: `tenants` alone carries
    several NOT NULL columns with Python-side defaults, and hand-writing the
    column list means this fixture breaks every time an unrelated column is
    added. The migration under test reads only `installment_items`,
    `catalog_models`, `catalog_categories` and `products`, so how the
    surrounding rows get there is not part of what is being proved.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import (
        CatalogBrand,
        CatalogCategory,
        CatalogModel,
        Customer,
        Installment,
        InstallmentItem,
        Product,
        Tenant,
        TenantSettings,
    )
    from app.models.enums import CustomerStatus, InstallmentStatus

    Session = async_sessionmaker(engine, expire_on_commit=False)
    ids: dict[str, str] = {}
    async with Session() as s:
        tenant = Tenant(slug="bf-shop", name="Backfill Shop")
        s.add(tenant); await s.flush()
        s.add(TenantSettings(tenant_id=tenant.id, currency="UZS"))
        customer = Customer(tenant_id=tenant.id, full_name="Buyer",
                            phone="+998901234701", status=CustomerStatus.active)
        s.add(customer); await s.flush()
        contract = Installment(
            tenant_id=tenant.id, customer_id=customer.id,
            status=InstallmentStatus.active, currency="UZS",
            total_amount=1_000_000, down_payment=0, principal=1_000_000,
            months=6, monthly_amount=166_667, start_date=date.today(),
        )
        s.add(contract); await s.flush()

        # The catalog is SEEDED by an earlier migration — `smartfonlar` and
        # `avtomobillar` are real production rows, not fixtures, and
        # `catalog_categories.slug` is unique. Reuse them; creating them again
        # would fail, and pinning our own copies would test a catalog that does
        # not exist in production.
        async def _category(slug: str, name_uz: str, business_type: str,
                            is_active: bool = True) -> CatalogCategory:
            found = (await s.execute(
                select(CatalogCategory).where(CatalogCategory.slug == slug)
            )).scalar_one_or_none()
            if found is not None:
                return found
            created = CatalogCategory(slug=slug, name_uz=name_uz, name_ru=name_uz,
                                      business_types=[business_type], is_active=is_active)
            s.add(created)
            await s.flush()
            return created

        phones = await _category("smartfonlar", "Smartfonlar", "electronics")
        await _category("avtomobillar", "Avtomobillar", "auto")
        # (MUDD-92 M1) Retired AFTER the sale — the exact case the API's
        # `_resolve_item_categories` must resolve the same way the backfill
        # does, since the two disagreeing is the bug this pins.
        retired = await _category("mudd83-retired", "Retired", "electronics",
                                  is_active=False)

        brand = CatalogBrand(slug="mudd83-fixture-brand", name="Fixture Brand",
                             category_id=phones.id, category_ids=[str(phones.id)])
        s.add(brand); await s.flush()

        model_phone = CatalogModel(brand_id=brand.id, category_id=phones.id,
                                   slug="mudd83-phone", name="Fixture Phone")
        # A model whose OWN category was never set. Its brand is filed under
        # `smartfonlar`; following that hop is exactly the mistake this file
        # asserts against, so a line pointing here must come out NULL.
        model_orphan = CatalogModel(brand_id=brand.id, category_id=None,
                                    slug="mudd83-mystery", name="Mystery")
        # A model whose category was retired between the sale and this deploy.
        # `is_active` must not matter to pass 1 — the row still exists.
        model_retired_category = CatalogModel(brand_id=brand.id, category_id=retired.id,
                                              slug="mudd83-retired-model", name="Retired-category Fixture")
        s.add_all([model_phone, model_orphan, model_retired_category]); await s.flush()

        product_car = Product(tenant_id=tenant.id, name_uz="Sovutgich",
                              category="avtomobillar", base_price=1, is_active=True)
        # `products.category` is free text and predates the catalog, so it can
        # hold a slug no category has.
        product_legacy = Product(tenant_id=tenant.id, name_uz="Eski mahsulot",
                                 category="a-slug-that-no-longer-exists",
                                 base_price=1, is_active=True)
        s.add_all([product_car, product_legacy]); await s.flush()

        lines = {
            "item_catalog": InstallmentItem(
                installment_id=contract.id, catalog_model_id=model_phone.id,
                product_name_snapshot="iPhone", qty=1,
                unit_price=1_000_000, line_total=1_000_000),
            "item_catalog_no_category": InstallmentItem(
                installment_id=contract.id, catalog_model_id=model_orphan.id,
                product_name_snapshot="Mystery", qty=1,
                unit_price=1_000_000, line_total=1_000_000),
            "item_catalog_retired_category": InstallmentItem(
                installment_id=contract.id, catalog_model_id=model_retired_category.id,
                product_name_snapshot="Retired-category Fixture", qty=1,
                unit_price=1_000_000, line_total=1_000_000),
            "item_product": InstallmentItem(
                installment_id=contract.id, product_id=product_car.id,
                product_name_snapshot="Sovutgich", qty=1,
                unit_price=1_000_000, line_total=1_000_000),
            "item_legacy_product": InstallmentItem(
                installment_id=contract.id, product_id=product_legacy.id,
                product_name_snapshot="Eski", qty=1,
                unit_price=1_000_000, line_total=1_000_000),
            "item_freetext": InstallmentItem(
                installment_id=contract.id,
                product_name_snapshot="Nomsiz", qty=1,
                unit_price=1_000_000, line_total=1_000_000),
        }
        s.add_all(lines.values()); await s.flush()
        ids = {name: str(row.id) for name, row in lines.items()}
        ids["product_legacy"] = str(product_legacy.id)
        await s.commit()
    return ids


async def _categories(engine, ids: dict[str, str]) -> dict[str, str | None]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT id, category FROM installment_items"))).all()
    by_id = {str(row[0]): row[1] for row in rows}
    return {name: by_id[item_id] for name, item_id in ids.items() if name.startswith("item_")}


async def test_the_backfill_categorises_history_it_can_and_admits_what_it_cannot(
    migrated_db,
) -> None:
    engine = migrated_db
    ids = await _seed(engine)

    # Take the column away and put it back — the path a deploy takes, and the
    # only way to see the backfill act on rows that already exist.
    await asyncio.to_thread(_alembic, PREVIOUS)
    async with engine.connect() as conn:
        assert (await conn.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'installment_items' AND column_name = 'category'"
        ))).scalar_one() == 0, "downgrade left the column behind"
    await asyncio.to_thread(_alembic, REVISION)

    got = await _categories(engine, ids)

    # Pass 1: the model's OWN category.
    assert got["item_catalog"] == "smartfonlar"

    # NOT via the brand. This model has no category of its own; its brand is
    # filed under `smartfonlar`, and following that hop would put a phone
    # brand's laptop in the phone bucket. A brand spans categories now
    # (`catalog_brands.category_ids`), so the hop is not merely imprecise —
    # it is wrong. NULL is the honest answer.
    assert got["item_catalog_no_category"] is None

    # (MUDD-92 M1) `is_active` does not gate pass 1 — the row is retired, not
    # gone, and the API's `_resolve_item_categories` must resolve this exact
    # input the same way (see test_a_catalog_derived_category_survives_the_
    # category_being_retired in test_mudd83_installment_category.py).
    assert got["item_catalog_retired_category"] == "mudd83-retired"

    # Pass 2: the tenant's own product row.
    assert got["item_product"] == "avtomobillar"

    # `products.category` is free text and predates the catalog. A slug no
    # category has would fragment the breakdown into buckets the filter can
    # never select, and the migration's own guard would have aborted the
    # deploy over it — so it is dropped rather than carried forward.
    assert got["item_legacy_product"] is None

    # Neither product nor catalog model. Nothing in the database knows what
    # this was, and guessing would put fiction into a revenue chart.
    assert got["item_freetext"] is None


async def test_the_index_the_filter_depends_on_is_created_and_removed(
    migrated_db,
) -> None:
    """The category filter is an EXISTS over installment_items.

    Without `ix_items_installment_category` that EXISTS scans every line item
    ever sold, on the busiest screen in the product. A migration that added the
    column but not the index would look correct and read slower every month.
    """
    engine = migrated_db

    async def _index_exists() -> bool:
        async with engine.connect() as conn:
            return (await conn.execute(text(
                "SELECT count(*) FROM pg_indexes "
                "WHERE tablename = 'installment_items' "
                "  AND indexname = 'ix_items_installment_category'"
            ))).scalar_one() == 1

    assert await _index_exists()
    await asyncio.to_thread(_alembic, PREVIOUS)
    assert not await _index_exists(), "downgrade left the index behind"
    await asyncio.to_thread(_alembic, REVISION)
    assert await _index_exists()


async def test_a_legacy_slug_on_a_product_row_does_not_abort_the_deploy(
    migrated_db,
) -> None:
    """Found by this test before it could find production.

    The first version of pass 2 copied `products.category` forward unfiltered
    and then aborted on anything the catalog did not recognise. Since that
    column is free text and predates the catalog, any tenant carrying one
    legacy value would have failed the deploy — for data they cannot see,
    describing contracts that are already closed.

    Worse, it disagreed with the API: `_resolve_item_categories` absorbs the
    same input by dropping it to NULL. A row's category would then have
    depended on which side of the deploy it was written on.

    Both now do the same thing, and the migration completes.
    """
    engine = migrated_db
    ids = await _seed(engine)
    await asyncio.to_thread(_alembic, PREVIOUS)
    await asyncio.to_thread(_alembic, REVISION)

    assert (await _categories(engine, ids))["item_legacy_product"] is None
