"""Clear catalog categories / brands / models (Round 41) — NEUTRALIZED (#76)

One-off data reset so the operator can enter the real catalog from scratch.
Only the catalog hierarchy is cleared — colours (catalog_colors) are kept.
References from installment_items / products to a model are ON DELETE SET NULL,
so historical sales are preserved (the snapshot name/variant stays on the row).

Revision ID: r41clearcatalog
Revises: r40morecolors
Create Date: 2026-05-31 15:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "r41clearcatalog"
down_revision = "r40morecolors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # (#76) Neutralized to a no-op. This was a one-off operator reset that
    # UNCONDITIONALLY DELETEd the whole catalog hierarchy. On a fresh install the
    # tables are empty so it did nothing useful, but if the chain were ever
    # re-applied with real data present it would silently wipe the entire catalog
    # — and the r42+ seeds are idempotent upserts that don't need a prior clear.
    # Made a no-op so the migration can never destroy data; environments that
    # already ran the original DELETE are unaffected.
    pass


def downgrade() -> None:
    # Deleted seed data cannot be restored.
    pass
