"""Make remaining event timestamps timezone-aware (R62)

Several columns were created as TIMESTAMP WITHOUT TIME ZONE in the initial
schema, but the app writes timezone-aware UTC datetimes (datetime.now(timezone.utc)).
asyncpg then raises "can't subtract offset-naive and offset-aware datetimes",
surfacing as a 500 internal_error — e.g. when sending a broadcast. This is the
same class of bug r33 fixed for tenant_subscriptions; here we convert the rest.

Each ALTER is guarded by an information_schema check so it only converts columns
that are still naive — re-running (or running where a column is already
timestamptz) is a no-op and never double-shifts the stored values.

Revision ID: r62tzaware
Revises: r61encsecrets
Create Date: 2026-05-31 14:20:00+00:00
"""
from __future__ import annotations

from alembic import op

revision = "r62tzaware"
down_revision = "r61encsecrets"
branch_labels = None
depends_on = None

# (table, [columns]) — every event timestamp the app writes a tz-aware value to.
COLS: list[tuple[str, list[str]]] = [
    ("broadcasts", ["scheduled_at", "started_at", "finished_at"]),
    ("broadcast_recipients", ["delivered_at"]),
    ("installments", ["closed_at"]),
    ("payments", ["paid_at", "approved_at"]),
    ("notifications", ["read_at"]),
    ("promo_codes", ["valid_from", "valid_until"]),
    ("ad_campaigns", ["started_at", "ended_at"]),
    ("customer_risk_signals", ["expires_at"]),
]


def upgrade() -> None:
    for table, cols in COLS:
        for col in cols:
            op.execute(
                f"""
                DO $$ BEGIN
                  IF (SELECT data_type FROM information_schema.columns
                      WHERE table_name = '{table}' AND column_name = '{col}')
                     = 'timestamp without time zone' THEN
                    ALTER TABLE {table}
                      ALTER COLUMN {col} TYPE timestamptz USING {col} AT TIME ZONE 'UTC';
                  END IF;
                END $$;
                """
            )


def downgrade() -> None:
    for table, cols in COLS:
        for col in cols:
            op.execute(
                f"ALTER TABLE {table} "
                f"ALTER COLUMN {col} TYPE timestamp USING {col} AT TIME ZONE 'UTC'"
            )
