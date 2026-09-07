"""Product cost precision + one active subscription per tenant (M-02, M-04)

M-02 — ``products.cost_price`` was declared as a bare ``Mapped[Decimal | None]``.
``Base`` carries no ``type_annotation_map``, so SQLAlchemy mapped it to an
UNCONSTRAINED ``NUMERIC`` (see ``7112558dc470:364``) while every other money
column in the schema is ``Numeric(14, 2)``. An unconstrained numeric accepts any
scale, so a cost could carry precision no other money column can round-trip.

M-04 — ``tenant_subscriptions`` had only ``UNIQUE (tenant_id, starts_at)``.
Two ``active`` rows with different start instants were therefore legal, and which
one entitlement/billing observed came down to query order. A PARTIAL unique index
makes "at most one live subscription" an invariant of the database instead of a
convention every writer has to remember. Non-active rows (``trial``,
``past_due``, ``cancelled``) stay unconstrained — that history IS the billing record.

Both steps PRE-CHECK and refuse rather than mutate silently: narrowing a numeric
scale ROUNDS, and a unique index over dirty data must be explained, not guessed at.

Revision ID: rh0numsub
Revises: rg0credsplit
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "rh0numsub"
down_revision = "rg0credsplit"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_tenant_sub_one_active"


def _assert_cost_price_fits_scale_2() -> None:
    """Refuse to narrow the scale while a stored cost would be ROUNDED by it.

    ``ALTER ... TYPE numeric(14,2)`` rounds anything with a finer scale — a silent
    money mutation. This is the same failure mode r68's downgrade had (M-03), so it
    gets the same answer: stop before touching a row and name what is in the way.
    """
    bind = op.get_bind()
    bad = bind.execute(sa.text(
        "SELECT count(*) FROM products "
        "WHERE cost_price IS NOT NULL AND cost_price <> round(cost_price, 2)"
    )).scalar_one()
    if bad:
        raise RuntimeError(
            f"rh0numsub refused: {bad} products.cost_price row(s) carry more than 2 "
            "decimal places and ALTER TYPE numeric(14,2) would ROUND them. Reconcile "
            "those rows first (SELECT id, cost_price FROM products "
            "WHERE cost_price <> round(cost_price, 2))."
        )


def _assert_one_active_subscription_per_tenant() -> None:
    """Refuse to create the partial unique index over data that already violates it."""
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT tenant_id, count(*) AS n FROM tenant_subscriptions "
        "WHERE status = 'active' GROUP BY tenant_id HAVING count(*) > 1 "
        "ORDER BY n DESC LIMIT 20"
    )).all()
    if rows:
        detail = ", ".join(f"{r.tenant_id}={r.n}" for r in rows)
        raise RuntimeError(
            "rh0numsub refused: tenant(s) already hold more than one ACTIVE "
            f"subscription ({detail}). Deciding which one is live is a billing "
            "decision, not a migration's: cancel the superseded rows first."
        )


def upgrade() -> None:
    _assert_cost_price_fits_scale_2()
    op.alter_column(
        "products", "cost_price",
        existing_type=sa.Numeric(), type_=sa.Numeric(14, 2),
        existing_nullable=True, postgresql_using="cost_price::numeric(14,2)",
    )

    _assert_one_active_subscription_per_tenant()
    op.create_index(
        INDEX_NAME, "tenant_subscriptions", ["tenant_id"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="tenant_subscriptions")
    # numeric(14,2) -> numeric is a WIDENING: every stored value round-trips, so
    # unlike r68's downgrade there is nothing here to refuse.
    op.alter_column(
        "products", "cost_price",
        existing_type=sa.Numeric(14, 2), type_=sa.Numeric(),
        existing_nullable=True,
    )
