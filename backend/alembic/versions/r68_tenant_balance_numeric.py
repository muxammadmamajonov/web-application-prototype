"""Tenant ledger money: Integer -> Numeric(14,2) + non-negativity CHECKs (#69)

The tenant prepaid-balance ledger stored money as INTEGER while every other
money column in the schema is Numeric(14,2). That inconsistency invites silent
int/Decimal truncation and implicit-cast bugs wherever ledger money meets the
rest of the money model (plans, installments, payments). This converts the four
ledger money columns to Numeric(14,2) and codifies their invariants — balance
and balance_after non-negative, top-up amount strictly positive — as CHECK
constraints.

These tables (tenants, tenant_balance_txns, tenant_topups) are small, so the
table-rewrite an ALTER TYPE incurs is cheap here; the large/destructive-migration
deploy-safety work is tracked separately (#76).

Revision ID: r68ledgernum
Revises: r67notifinst
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "r68ledgernum"
down_revision = "r67notifinst"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # INTEGER -> NUMERIC(14,2). USING makes the cast explicit — Postgres will not
    # implicitly change a column's type without it.
    op.alter_column(
        "tenants", "balance",
        existing_type=sa.Integer(), type_=sa.Numeric(14, 2),
        existing_nullable=False, postgresql_using="balance::numeric(14,2)",
    )
    op.alter_column(
        "tenant_balance_txns", "amount",
        existing_type=sa.Integer(), type_=sa.Numeric(14, 2),
        existing_nullable=False, postgresql_using="amount::numeric(14,2)",
    )
    op.alter_column(
        "tenant_balance_txns", "balance_after",
        existing_type=sa.Integer(), type_=sa.Numeric(14, 2),
        existing_nullable=False, postgresql_using="balance_after::numeric(14,2)",
    )
    op.alter_column(
        "tenant_topups", "amount",
        existing_type=sa.Integer(), type_=sa.Numeric(14, 2),
        existing_nullable=False, postgresql_using="amount::numeric(14,2)",
    )

    op.create_check_constraint(
        op.f("ck_tenants_balance_nonneg"), "tenants", "balance >= 0",
    )
    op.create_check_constraint(
        op.f("ck_tenant_balance_txns_balance_after_nonneg"),
        "tenant_balance_txns", "balance_after >= 0",
    )
    op.create_check_constraint(
        op.f("ck_tenant_topups_amount_positive"), "tenant_topups", "amount > 0",
    )


#: (M-03) The four columns this migration widened, as ``(table, column)``.
_LEDGER_COLS: tuple[tuple[str, str], ...] = (
    ("tenants", "balance"),
    ("tenant_balance_txns", "amount"),
    ("tenant_balance_txns", "balance_after"),
    ("tenant_topups", "amount"),
)


def _assert_no_fractional_money() -> None:
    """(M-03) Refuse to downgrade while any ledger row holds tiyin.

    ``NUMERIC -> INTEGER`` with ``USING <col>::integer`` does not truncate — Postgres
    ROUNDS. A balance of 100.75 comes back as 101, and nothing downstream recomputes
    it, so the tenant is simply 0.25 richer (or poorer) than the payments that
    produced the number. That is an unreviewable money mutation performed by a
    schema rollback.

    A rollback of a *type widening* is only ever safe when no value has used the
    extra precision yet — the case this migration is actually rolled back in
    (a bad deploy, minutes later). So that case still works, and the case that
    would lose money stops, loudly, before touching a single row.
    """
    bind = op.get_bind()
    offenders = []
    for table, col in _LEDGER_COLS:
        n = bind.execute(
            sa.text(f'SELECT count(*) FROM "{table}" WHERE "{col}" <> trunc("{col}")')  # noqa: S608
        ).scalar_one()
        if n:
            offenders.append(f"{table}.{col} ({n} row(s))")
    if offenders:
        raise RuntimeError(
            "r68 downgrade refused: NUMERIC->INTEGER would ROUND fractional money "
            "and no downgrade path recomputes the balances. Fractional values in: "
            + ", ".join(offenders)
            + ". Reconcile or archive these rows first; see docs/RUNBOOK.md."
        )


def downgrade() -> None:
    _assert_no_fractional_money()

    op.drop_constraint(op.f("ck_tenant_topups_amount_positive"), "tenant_topups", type_="check")
    op.drop_constraint(
        op.f("ck_tenant_balance_txns_balance_after_nonneg"),
        "tenant_balance_txns", type_="check",
    )
    op.drop_constraint(op.f("ck_tenants_balance_nonneg"), "tenants", type_="check")

    op.alter_column(
        "tenant_topups", "amount",
        existing_type=sa.Numeric(14, 2), type_=sa.Integer(),
        existing_nullable=False, postgresql_using="amount::integer",
    )
    op.alter_column(
        "tenant_balance_txns", "balance_after",
        existing_type=sa.Numeric(14, 2), type_=sa.Integer(),
        existing_nullable=False, postgresql_using="balance_after::integer",
    )
    op.alter_column(
        "tenant_balance_txns", "amount",
        existing_type=sa.Numeric(14, 2), type_=sa.Integer(),
        existing_nullable=False, postgresql_using="amount::integer",
    )
    op.alter_column(
        "tenants", "balance",
        existing_type=sa.Numeric(14, 2), type_=sa.Integer(),
        existing_nullable=False, postgresql_using="balance::integer",
    )
