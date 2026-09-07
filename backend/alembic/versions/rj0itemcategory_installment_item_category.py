"""installment_items.category — what kind of thing each line actually sold.

(MUDD-83) The contract ledger could not answer "how much did we sell in
phones this month". Not slowly — at all.

`reports.products()` already groups by category, but it reads it through
`Product.category` with an OUTER JOIN, so every line written from the global
catalog or as free text (`product_id IS NULL`) collapses into a single `"—"`
bucket. Those are not edge cases: the catalog path is how a tenant records a
sale of something it does not stock, which is most of what a small shop sells
on credit.

This adds the category to the line itself, as a SNAPSHOT — a `String(64)`
holding a `catalog_categories.slug`, not a foreign key. Catalog rows get
renamed, re-slugged, merged and deleted; a contract from two years ago still
has to be able to say what was sold under it. That is the same reasoning that
put `product_name_snapshot` next to a nullable `product_id`.

BACKFILL, in two passes, most specific first:

  1. `catalog_model_id -> catalog_models.category_id -> catalog_categories.slug`
     The reliable edge. NOT via `catalog_brands`: a brand now spans several
     categories (`catalog_brands.category_ids` is the source of truth and
     `category_id` is a legacy "primary"), so the brand hop would assign a
     phone brand's laptop to `smartfonlar`.
  2. `product_id -> products.category`, but ONLY where that value resolves to
     a live category. `_hydrate_from_catalog` writes real slugs there, but the
     column is free text and predates the catalog, so a tenant can be carrying
     something nothing recognises. Copying that forward would create a
     breakdown bucket the category filter can never select. Dropping it to NULL
     is also what the API's `_resolve_item_categories` does with the same
     input — if the two disagreed, a row's category would depend on which side
     of this deploy it was written on.

Rows that neither pass can resolve keep NULL, and that is the correct answer.
They are free-text lines with no product and no catalog model; there is nothing
in the database that knows what they were. Guessing would put made-up numbers
into a revenue breakdown, which is worse than an honest "uncategorised".

Column NULLable and index non-CONCURRENT: `ADD COLUMN` with no default is
metadata-only in PostgreSQL 11+ (no table rewrite), and the index is built
inside the normal migration transaction. `installment_items` is small enough
for that today; `rh1paytenantidx` used CONCURRENTLY because `payments` is the
fastest-growing table and could not take the lock. If this table ever reaches
that size, the follow-up is a CONCURRENTLY rebuild, not a change here.
"""
from alembic import op

revision = "rj0itemcategory"
down_revision = "rh1paytenantidx"
branch_labels = None
depends_on = None


def _log(message: str) -> None:
    print(f"[rj0itemcategory] {message}")


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    bind = op.get_bind()

    total = bind.exec_driver_sql("SELECT count(*) FROM installment_items").scalar()

    op.execute("ALTER TABLE installment_items ADD COLUMN category VARCHAR(64)")

    # Pass 1 — the catalog edge, via the MODEL's own category.
    op.execute(
        """
        UPDATE installment_items AS ii
           SET category = cc.slug
          FROM catalog_models AS cm
          JOIN catalog_categories AS cc ON cc.id = cm.category_id
         WHERE ii.catalog_model_id = cm.id
           AND ii.category IS NULL
        """
    )
    from_catalog = bind.exec_driver_sql(
        "SELECT count(*) FROM installment_items WHERE category IS NOT NULL"
    ).scalar()

    # Pass 2 — the tenant's own product row, for whatever pass 1 could not
    # resolve (no catalog model, or a model whose category_id is NULL).
    #
    # Only values that resolve to a LIVE category. `products.category` is free
    # text and predates the catalog, so a tenant can be carrying a slug nothing
    # recognises. Copying it forward would create a breakdown bucket the
    # category filter can never select — real-looking data that is permanently
    # unreachable. Leaving those rows NULL is the same answer the API's
    # `_resolve_item_categories` gives for the same input, which matters:
    # if the two disagreed, a row's category would depend on whether it was
    # written before or after this deploy.
    op.execute(
        """
        UPDATE installment_items AS ii
           SET category = p.category
          FROM products AS p
         WHERE ii.product_id = p.id
           AND ii.category IS NULL
           AND NULLIF(btrim(p.category), '') IS NOT NULL
           AND EXISTS (
               SELECT 1 FROM catalog_categories cc WHERE cc.slug = p.category
           )
        """
    )
    resolved = bind.exec_driver_sql(
        "SELECT count(*) FROM installment_items WHERE category IS NOT NULL"
    ).scalar()
    _log(
        f"items={total} categorised={resolved} "
        f"(catalog={from_catalog}, products={resolved - from_catalog}, "
        f"uncategorised={total - resolved})"
    )

    # A post-condition, not a filter — both passes above already constrain
    # themselves to live categories, so this should be unreachable. It stays
    # because the cost of being wrong here is permanent and silent: a slug the
    # category filter can never select would sit in the breakdown looking like
    # real data forever, and nothing downstream would ever question it. A
    # concurrent catalog edit between the UPDATE and this check, or a future
    # third pass added without the same constraint, would both land here.
    #
    # It aborts rather than repairs, deliberately: reaching this means an
    # assumption in this file is wrong, and quietly NULLing the rows would hide
    # that instead of surfacing it.
    orphaned = bind.exec_driver_sql(
        """
        SELECT count(*) FROM installment_items ii
         WHERE ii.category IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM catalog_categories cc WHERE cc.slug = ii.category
           )
        """
    ).scalar()
    if orphaned:
        raise RuntimeError(
            f"rj0itemcategory: {orphaned} item(s) were backfilled with a slug that "
            f"matches no catalog_categories row. products.category is free text and "
            f"may hold a legacy value. Nothing has been committed — reconcile those "
            f"slugs (or clear them) and re-run."
        )
    _log("every backfilled slug resolves to a live catalog category")

    # Leading with installment_id, not category: both readers (the list
    # endpoint's EXISTS filter and the overview's aggregate) arrive holding
    # installment ids and ask what categories they carry.
    op.execute(
        "CREATE INDEX ix_items_installment_category "
        "ON installment_items (installment_id, category)"
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    op.execute("DROP INDEX IF EXISTS ix_items_installment_category")
    op.execute("ALTER TABLE installment_items DROP COLUMN IF EXISTS category")
