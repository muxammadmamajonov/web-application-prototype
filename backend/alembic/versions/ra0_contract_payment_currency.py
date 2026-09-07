"""Persist the currency on every contract and payment.

Revision ID: ra0currency
Revises: r99globalrls

Currency becomes a property of the contract, snapshot at creation, instead of a
render-time read of the shop's current setting. Changing a shop's currency then
affects only new contracts. No amount is converted anywhere — this migration
copies a label onto rows.

Backfill policy: a contract inherits its tenant's configured currency, a payment
inherits its parent contract, and a tenant with no settings row gets UZS.

FAIL CLOSED. That backfill is only correct where the tenant's CURRENT setting is
also the one its existing contracts were written under. Every case where that
cannot be established aborts the migration before a single row is written:

  * a tenant whose audit trail shows a currency change with contracts predating
    it — their original currency is not recoverable from the current setting;
  * a configured currency outside the supported set — coercing it to UZS would
    silently relabel real money.

Each abort raises with tenant ids and counts so an operator can resolve it by
hand. Nothing is written first: the inventory runs before any DDL, and alembic
is configured `transaction_per_migration`, so a raise leaves the database at
r99globalrls with no columns, no data and no version bump.

A payment must be denominated in its contract's currency. That is held by a
composite foreign key `(installment_id, currency) → installments(id, currency)`,
which is why the parent carries a unique key redundant against its primary key.
"""
from alembic import op

revision = "ra0currency"
down_revision = "r99globalrls"
branch_labels = None
depends_on = None

#: The currencies a shop may hold. Mirrors `app.core.currency` and the
#: frontend; the CHECK constraints below make the database the boundary neither
#: of the other two can drift past.
SUPPORTED = ("UZS", "USD")
_ALLOWED_SQL = ", ".join(f"'{c}'" for c in SUPPORTED)

#: What a tenant with no usable configured currency is written in.
DEFAULT_CURRENCY = "UZS"

#: A settings row is "configured" only when it holds a non-blank currency.
#: NULL and whitespace mean never chosen, which is the documented default — not
#: an unsupported value.
_CONFIGURED = "NULLIF(btrim(ts.currency), '') IS NOT NULL"


def _log(message: str) -> None:
    print(f"[ra0currency] {message}")


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    bind = op.get_bind()

    # ── 1. inventory, before any DDL or write ──────────────────────────────
    total_inst = bind.exec_driver_sql("SELECT count(*) FROM installments").scalar()
    total_pay = bind.exec_driver_sql("SELECT count(*) FROM payments").scalar()
    _log(f"installments={total_inst} payments={total_pay}")

    # ── 2. abort on any configured currency we cannot hold ─────────────────
    # Checked across ALL settings rows, not only those with contracts: an
    # unsupported value also decides what the tenant's NEXT contract is written
    # in, and the CHECK constraint added below would reject it. Coercing it to
    # UZS here is the silent relabel this whole change exists to stop.
    unsupported = bind.exec_driver_sql(
        f"""
        SELECT ts.tenant_id,
               upper(btrim(ts.currency)) AS configured,
               (SELECT count(*) FROM installments i WHERE i.tenant_id = ts.tenant_id) AS contracts
          FROM tenant_settings ts
         WHERE {_CONFIGURED}
           AND upper(btrim(ts.currency)) NOT IN ({_ALLOWED_SQL})
         ORDER BY ts.tenant_id
        """
    ).all()
    if unsupported:
        detail = "; ".join(
            f"tenant={tid} configured={cur!r} contracts={n}" for tid, cur, n in unsupported
        )
        raise RuntimeError(
            f"ra0currency: {len(unsupported)} tenant(s) hold a currency outside "
            f"{SUPPORTED}. Refusing to coerce it to {DEFAULT_CURRENCY} — that "
            f"would relabel real money. Correct tenant_settings.currency for "
            f"each, then re-run. [{detail}]"
        )

    # ── 3. abort where a tenant's history says the current setting is wrong ─
    # A tenant that switched currency at time T wrote its pre-T contracts in the
    # OLD one. Backfilling those from the CURRENT setting mislabels them, and the
    # audit trail cannot be assumed complete enough to reconstruct each contract's
    # currency, so this is a manual resolution rather than a guess.
    #
    # Contracts created after the latest audited change ARE safely the current
    # currency, so a tenant whose whole history postdates its last switch is not
    # affected and does not block the deploy.
    conflicts = bind.exec_driver_sql(
        """
        WITH switched AS (
            SELECT tenant_id, max(created_at) AS changed_at
              FROM audit_log
             WHERE tenant_id IS NOT NULL
               AND (before ->> 'currency') IS NOT NULL
               AND (before ->> 'currency') IS DISTINCT FROM (after ->> 'currency')
             GROUP BY tenant_id
        )
        SELECT s.tenant_id, s.changed_at,
               count(i.id) FILTER (WHERE i.created_at < s.changed_at) AS before_change,
               count(i.id) AS total
          FROM switched s
          JOIN installments i ON i.tenant_id = s.tenant_id
         GROUP BY s.tenant_id, s.changed_at
        HAVING count(i.id) FILTER (WHERE i.created_at < s.changed_at) > 0
         ORDER BY s.tenant_id
        """
    ).all()
    if conflicts:
        detail = "; ".join(
            f"tenant={tid} last_change={when.isoformat()} "
            f"contracts_predating_change={before} contracts_total={total}"
            for tid, when, before, total in conflicts
        )
        raise RuntimeError(
            f"ra0currency: {len(conflicts)} tenant(s) changed currency after "
            f"writing contracts. Those contracts' original currency is not "
            f"recoverable from the current setting, and backfilling them from it "
            f"would relabel signed obligations. Set installments.currency for "
            f"each affected contract by hand, then re-run. [{detail}]"
        )

    audited_changes = bind.exec_driver_sql(
        """
        SELECT count(DISTINCT tenant_id) FROM audit_log
         WHERE tenant_id IS NOT NULL
           AND (before ->> 'currency') IS NOT NULL
           AND (before ->> 'currency') IS DISTINCT FROM (after ->> 'currency')
        """
    ).scalar()
    _log(f"tenants with an audited currency change: {audited_changes} "
         f"(none with contracts predating the change)")

    # ── 4. the documented default, counted rather than assumed ─────────────
    unconfigured = bind.exec_driver_sql(
        f"""
        SELECT DISTINCT i.tenant_id
          FROM installments i
          LEFT JOIN tenant_settings ts ON ts.tenant_id = i.tenant_id
         WHERE ts.tenant_id IS NULL OR NOT ({_CONFIGURED})
         ORDER BY 1
        """
    ).scalars().all()
    unconfigured_rows = bind.exec_driver_sql(
        f"""
        SELECT count(*)
          FROM installments i
          LEFT JOIN tenant_settings ts ON ts.tenant_id = i.tenant_id
         WHERE ts.tenant_id IS NULL OR NOT ({_CONFIGURED})
        """
    ).scalar()
    _log(f"tenants with contracts but no configured currency: "
         f"{len(unconfigured)} ({unconfigured_rows} contracts → {DEFAULT_CURRENCY}) "
         f"{[str(t) for t in unconfigured]}")

    by_currency = bind.exec_driver_sql(
        f"""
        SELECT COALESCE(NULLIF(upper(btrim(ts.currency)), ''), '{DEFAULT_CURRENCY}') AS cur,
               count(*)
          FROM installments i
          LEFT JOIN tenant_settings ts ON ts.tenant_id = i.tenant_id
         GROUP BY 1 ORDER BY 1
        """
    ).all()
    _log(f"planned backfill by currency: {[(c, n) for c, n in by_currency]}")

    # ── 5. columns + backfill ──────────────────────────────────────────────
    op.execute("ALTER TABLE installments ADD COLUMN currency VARCHAR(8)")
    op.execute("ALTER TABLE payments     ADD COLUMN currency VARCHAR(8)")

    op.execute(
        """
        UPDATE installments i
           SET currency = upper(btrim(ts.currency))
          FROM tenant_settings ts
         WHERE ts.tenant_id = i.tenant_id
           AND NULLIF(btrim(ts.currency), '') IS NOT NULL
        """
    )
    op.execute(
        f"UPDATE installments SET currency = '{DEFAULT_CURRENCY}' WHERE currency IS NULL"
    )
    op.execute(
        """
        UPDATE payments p
           SET currency = i.currency
          FROM installments i
         WHERE i.id = p.installment_id
        """
    )

    # ── 6. zero unresolved, or stop ────────────────────────────────────────
    left_inst = bind.exec_driver_sql(
        "SELECT count(*) FROM installments WHERE currency IS NULL"
    ).scalar()
    left_pay = bind.exec_driver_sql(
        "SELECT count(*) FROM payments WHERE currency IS NULL"
    ).scalar()
    if left_inst or left_pay:
        raise RuntimeError(
            f"ra0currency: refusing NOT NULL with unresolved rows — "
            f"{left_inst} installments, {left_pay} payments still NULL"
        )
    stray = bind.exec_driver_sql(
        f"SELECT count(*) FROM installments WHERE currency NOT IN ({_ALLOWED_SQL})"
    ).scalar()
    if stray:
        raise RuntimeError(
            f"ra0currency: {stray} contract(s) resolved to a currency outside "
            f"{SUPPORTED} after backfill. Step 2 should have caught this; the "
            f"data changed underneath the migration. Nothing has been committed."
        )
    _log("unresolved after backfill: installments=0 payments=0")

    # ── 7. lock it down ────────────────────────────────────────────────────
    op.execute("ALTER TABLE installments ALTER COLUMN currency SET NOT NULL")
    op.execute("ALTER TABLE payments     ALTER COLUMN currency SET NOT NULL")
    op.execute(
        f"ALTER TABLE installments ADD CONSTRAINT ck_installments_currency "
        f"CHECK (currency IN ({_ALLOWED_SQL}))"
    )
    op.execute(
        f"ALTER TABLE payments ADD CONSTRAINT ck_payments_currency "
        f"CHECK (currency IN ({_ALLOWED_SQL}))"
    )

    # The unique key is redundant against the primary key by design: it is the
    # target the composite foreign key needs, and it is what makes "a payment is
    # in its contract's currency" an invariant of the database.
    op.execute(
        "ALTER TABLE installments ADD CONSTRAINT uq_installments_id_currency "
        "UNIQUE (id, currency)"
    )
    op.execute(
        "ALTER TABLE payments ADD CONSTRAINT fk_payments_contract_currency "
        "FOREIGN KEY (installment_id, currency) "
        "REFERENCES installments (id, currency) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    op.execute("ALTER TABLE payments DROP CONSTRAINT IF EXISTS fk_payments_contract_currency")
    op.execute("ALTER TABLE installments DROP CONSTRAINT IF EXISTS uq_installments_id_currency")
    op.execute("ALTER TABLE payments DROP CONSTRAINT IF EXISTS ck_payments_currency")
    op.execute("ALTER TABLE installments DROP CONSTRAINT IF EXISTS ck_installments_currency")
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS currency")
    op.execute("ALTER TABLE installments DROP COLUMN IF EXISTS currency")
