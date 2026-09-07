"""DB hardening: JSONB, money CHECKs, composite/FK indexes, trgm search,
contract-number uniqueness, and an updated_at trigger.

Production-readiness pass (senior DB audit):
  • Convert every ``json`` column to ``jsonb`` (indexable, compact, faster reads).
  • Add money invariants as CHECK constraints (NOT VALID → VALIDATE).
  • Add composite + foreign-key indexes for the hot list queries and to stop
    parent-delete sequential scans.
  • Enable pg_trgm + a GIN index so the single customer search box scales.
  • Enforce per-tenant unique contract numbers.
  • Add a BEFORE UPDATE trigger so raw SQL UPDATEs also bump ``updated_at``.

Revision ID: r64hardening
Revises: r63tzchannel
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r64hardening"
down_revision = "r63tzchannel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. JSON → JSONB (every json column in public, by introspection) ──────
    json_cols = conn.execute(sa.text("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND data_type = 'json'
        ORDER BY table_name, column_name
    """)).fetchall()
    for tbl, col in json_cols:
        op.execute(
            f'ALTER TABLE "{tbl}" ALTER COLUMN "{col}" TYPE jsonb USING "{col}"::jsonb'
        )

    # ── 2. Money invariants (NOT VALID first → no long lock, then VALIDATE) ──
    checks = [
        ("installments", "ck_installments_amounts_nonneg",
         "total_amount >= 0 AND principal >= 0 AND down_payment >= 0 "
         "AND monthly_amount >= 0 AND paid_amount >= 0"),
        ("installments", "ck_installments_months_pos", "months > 0"),
        ("installments", "ck_installments_down_le_total", "down_payment <= total_amount"),
        ("payments", "ck_payments_amount_pos", "amount > 0"),
        ("installment_items", "ck_installment_items_qty_price_valid",
         "qty > 0 AND unit_price >= 0 AND line_total >= 0"),
    ]
    for tbl, name, expr in checks:
        op.execute(f'ALTER TABLE {tbl} ADD CONSTRAINT {name} CHECK ({expr}) NOT VALID')
        op.execute(f'ALTER TABLE {tbl} VALIDATE CONSTRAINT {name}')

    # ── 3. Composite + foreign-key indexes ───────────────────────────────────
    index_sql = [
        # composites matching WHERE tenant_id [AND status] ORDER BY date
        "CREATE INDEX IF NOT EXISTS ix_customers_tenant_created ON customers (tenant_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_installments_tenant_status_due ON installments (tenant_id, status, next_due_date)",
        "CREATE INDEX IF NOT EXISTS ix_installments_tenant_created ON installments (tenant_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_payments_tenant_status_created ON payments (tenant_id, status, created_at)",
        # unindexed foreign keys (parent-delete scans + JOINs)
        "CREATE INDEX IF NOT EXISTS ix_installments_sold_by ON installments (sold_by_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_items_product ON installment_items (product_id)",
        "CREATE INDEX IF NOT EXISTS ix_items_catalog_model ON installment_items (catalog_model_id)",
        "CREATE INDEX IF NOT EXISTS ix_payments_submitted_by ON payments (submitted_by_user_id)",
        "CREATE INDEX IF NOT EXISTS ix_payments_approved_by ON payments (approved_by_user_id)",
    ]
    for stmt in index_sql:
        op.execute(stmt)

    # ── 4. Per-tenant unique contract number (NULL drafts allowed) ───────────
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_installments_tenant_contract "
        "ON installments (tenant_id, contract_number) WHERE contract_number IS NOT NULL"
    )

    # ── 5. pg_trgm search + JSONB GIN ────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_customers_fullname_trgm "
        "ON customers USING gin (lower(full_name) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_attributes_gin "
        "ON products USING gin (attributes jsonb_path_ops)"
    )

    # ── 6. updated_at trigger — raw UPDATEs bump it, not only the ORM ────────
    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN NEW.updated_at = now(); RETURN NEW; END;
        $$ LANGUAGE plpgsql
    """)
    ts_tables = conn.execute(sa.text("""
        SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'updated_at'
        ORDER BY table_name
    """)).fetchall()
    for (tbl,) in ts_tables:
        op.execute(f'DROP TRIGGER IF EXISTS trg_set_updated_at ON "{tbl}"')
        op.execute(
            f'CREATE TRIGGER trg_set_updated_at BEFORE UPDATE ON "{tbl}" '
            f'FOR EACH ROW EXECUTE FUNCTION set_updated_at()'
        )


def downgrade() -> None:
    conn = op.get_bind()

    ts_tables = conn.execute(sa.text("""
        SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'updated_at'
    """)).fetchall()
    for (tbl,) in ts_tables:
        op.execute(f'DROP TRIGGER IF EXISTS trg_set_updated_at ON "{tbl}"')
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")

    for idx in (
        "ix_products_attributes_gin", "ix_customers_fullname_trgm",
        "uq_installments_tenant_contract",
        "ix_payments_approved_by", "ix_payments_submitted_by",
        "ix_items_catalog_model", "ix_items_product", "ix_installments_sold_by",
        "ix_payments_tenant_status_created", "ix_installments_tenant_created",
        "ix_installments_tenant_status_due", "ix_customers_tenant_created",
    ):
        op.execute(f"DROP INDEX IF EXISTS {idx}")

    for tbl, name in (
        ("installment_items", "ck_installment_items_qty_price_valid"),
        ("payments", "ck_payments_amount_pos"),
        ("installments", "ck_installments_down_le_total"),
        ("installments", "ck_installments_months_pos"),
        ("installments", "ck_installments_amounts_nonneg"),
    ):
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {name}")
    # JSONB → JSON downgrade intentionally omitted (lossless to keep jsonb).
