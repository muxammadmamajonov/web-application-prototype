"""Proved phone ownership, and one Telegram account per customer per shop.

Revision ID: rc0verifiedid
Revises: rb0identtype
Create Date: 2026-08-10

Additive. Nothing existing is reinterpreted:

  * `users.phone` keeps its meaning — what somebody typed. It is NOT copied
    into `verified_phone`. A stored number is evidence that a form was filled
    in, never that the person can answer it, and promoting one would manufacture
    exactly the claim the new column exists to make trustworthy.
  * `customers.telegram_id` keeps whatever links already exist. They were
    created by an invitation claim, which is a real (if weaker) binding, so they
    are preserved as-is and simply become unique.

The one thing this migration can refuse is a duplicate: two customer rows in one
shop already sharing a Telegram account. That cannot be resolved from here —
picking one would attach a stranger's contracts to somebody's account — so it is
reported and the migration STOPS. Alembic runs one transaction per migration on
Postgres, so raising rolls back the DDL and the version bump together and the
database is left exactly as it was.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "rc0verifiedid"
down_revision = "rb0identtype"
branch_labels = None
depends_on = None


def _identity(column: str) -> str:
    """Mirror of `app.models.base.phone_identity_sql`. Kept literal on purpose:
    a migration must keep meaning what it meant when it ran, even after the
    application helper changes."""
    digits = f"regexp_replace({column}, '[^0-9]', '', 'g')"
    return (
        f"(CASE WHEN length({digits}) = 9 "
        f"THEN '998' || {digits} "
        f"ELSE {digits} END)"
    )


def upgrade() -> None:
    conn = op.get_bind()

    # ── inventory, before anything is changed ────────────────────────────────
    users = conn.execute(sa.text("SELECT count(*) FROM users")).scalar_one()
    typed = conn.execute(sa.text(
        "SELECT count(*) FROM users WHERE phone IS NOT NULL"
    )).scalar_one()
    linked = conn.execute(sa.text(
        "SELECT count(*) FROM customers WHERE telegram_id IS NOT NULL"
    )).scalar_one()
    print(f"[rc0verifiedid] users={users} with a typed phone={typed} "
          f"(none become verified) · customer↔telegram links preserved={linked}")

    # ── the conflict that cannot be resolved here ────────────────────────────
    dupes = conn.execute(sa.text(
        "SELECT tenant_id, telegram_id, count(*) AS n FROM customers "
        "WHERE telegram_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1 "
        "ORDER BY n DESC"
    )).all()
    if dupes:
        # Identifiers only — a migration log is not a place for names or phones.
        for tenant_id, _telegram_id, n in dupes[:20]:
            print(f"[rc0verifiedid]   tenant={tenant_id} has {n} customers sharing one Telegram account")
        raise RuntimeError(
            f"[rc0verifiedid] {len(dupes)} tenant/Telegram pair(s) are held by more than one "
            "customer row. Merging or deleting them is a business decision with contract "
            "history attached, so this migration will not guess. Resolve the listed "
            "tenants, then re-run. Nothing has been changed."
        )

    # ── proved-ownership columns ─────────────────────────────────────────────
    op.add_column("users", sa.Column("verified_phone", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("verified_phone_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("verified_phone_source", sa.String(24), nullable=True))
    op.add_column("users", sa.Column("phone_eligibility", sa.String(24), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(120), nullable=True))

    # A proved number identifies one person. Same digit rule as the typed-phone
    # index, so the two cannot disagree about what one number is.
    op.execute(
        "CREATE UNIQUE INDEX uq_users_verified_phone_digits ON users "
        f"(({_identity('verified_phone')})) WHERE verified_phone IS NOT NULL"
    )

    # A proved number always carries how it was proved and what it means. A row
    # with a value and no provenance would be indistinguishable from a typed one.
    op.create_check_constraint(
        "ck_users_verified_phone_provenance", "users",
        "(verified_phone IS NULL) = (verified_phone_source IS NULL) AND "
        "(verified_phone IS NULL OR verified_phone_at IS NOT NULL)",
    )

    op.execute(
        "CREATE UNIQUE INDEX uq_customer_tenant_telegram ON customers "
        "(tenant_id, telegram_id) WHERE telegram_id IS NOT NULL"
    )
    print("[rc0verifiedid] verified-phone columns added; no row was marked verified")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_customer_tenant_telegram")
    op.drop_constraint("ck_users_verified_phone_provenance", "users", type_="check")
    op.execute("DROP INDEX IF EXISTS uq_users_verified_phone_digits")
    for col in ("display_name", "phone_eligibility", "verified_phone_source",
                "verified_phone_at", "verified_phone"):
        op.drop_column("users", col)
