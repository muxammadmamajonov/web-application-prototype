"""State what kind of identifier a contract line's serial number is.

Revision ID: rb0identtype
Revises: ra0currency

`installment_items.serial_number` held either a device IMEI or a shop's own
serial / paper contract reference, with nothing recording which. The two have
different validation rules, so neither could be enforced.

This adds `identifier_type` alongside the existing value. It does NOT touch
`serial_number`: no value is rewritten, normalised or discarded.

Every existing non-empty identifier is classified `LEGACY_UNVERIFIED` — recorded
before the distinction existed and never validated under either rule. It is
deliberately not inferred from content: fifteen digits is not evidence of an
IMEI, and promoting a row on that basis would attach a device-identity claim
nobody checked. A shop reclassifies by editing the line.

No uniqueness constraint is added. Duplicate identifiers are surfaced as a
warning scoped to the tenant (plus a cross-tenant count), because the same
serial legitimately recurs across shops and a global unique index would reject
honest data.
"""
from alembic import op

revision = "rb0identtype"
down_revision = "ra0currency"
branch_labels = None
depends_on = None

#: Mirrors `app.core.identifiers.IdentifierType`. The CHECK below makes the
#: database the boundary the API and the UI cannot drift past.
TYPES = ("IMEI", "SERIAL_OR_CONTRACT", "LEGACY_UNVERIFIED")
_ALLOWED_SQL = ", ".join(f"'{t}'" for t in TYPES)

LEGACY = "LEGACY_UNVERIFIED"


def _log(message: str) -> None:
    print(f"[rb0identtype] {message}")


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    bind = op.get_bind()

    total = bind.exec_driver_sql("SELECT count(*) FROM installment_items").scalar()
    with_value = bind.exec_driver_sql(
        "SELECT count(*) FROM installment_items "
        "WHERE NULLIF(btrim(serial_number), '') IS NOT NULL"
    ).scalar()
    _log(f"items={total} carrying an identifier={with_value} → {LEGACY}")

    op.execute("ALTER TABLE installment_items ADD COLUMN identifier_type VARCHAR(24)")
    op.execute(
        f"UPDATE installment_items SET identifier_type = '{LEGACY}' "
        f"WHERE NULLIF(btrim(serial_number), '') IS NOT NULL"
    )

    # A line either carries both an identifier and its type, or neither. A type
    # with no value, or a value with no type, is the ambiguity this removes.
    stray = bind.exec_driver_sql(
        "SELECT count(*) FROM installment_items "
        "WHERE (NULLIF(btrim(serial_number), '') IS NOT NULL) "
        "   <> (identifier_type IS NOT NULL)"
    ).scalar()
    if stray:
        raise RuntimeError(
            f"rb0identtype: {stray} item(s) would end up with an identifier and no "
            f"type, or a type and no identifier. Nothing has been committed."
        )
    _log("value/type pairing verified for every row")

    op.execute(
        f"ALTER TABLE installment_items ADD CONSTRAINT ck_installment_items_identifier_type "
        f"CHECK (identifier_type IS NULL OR identifier_type IN ({_ALLOWED_SQL}))"
    )
    op.execute(
        "ALTER TABLE installment_items ADD CONSTRAINT ck_installment_items_identifier_paired "
        "CHECK ((NULLIF(btrim(serial_number), '') IS NOT NULL) = (identifier_type IS NOT NULL))"
    )
    # The duplicate warning scans by value within a tenant; without this it is a
    # sequential scan of every line item ever sold.
    op.execute(
        "CREATE INDEX ix_items_serial_number ON installment_items (serial_number) "
        "WHERE serial_number IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    op.execute("DROP INDEX IF EXISTS ix_items_serial_number")
    op.execute("ALTER TABLE installment_items DROP CONSTRAINT IF EXISTS ck_installment_items_identifier_paired")
    op.execute("ALTER TABLE installment_items DROP CONSTRAINT IF EXISTS ck_installment_items_identifier_type")
    op.execute("ALTER TABLE installment_items DROP COLUMN IF EXISTS identifier_type")
