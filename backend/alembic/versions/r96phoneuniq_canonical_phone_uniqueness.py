"""One phone = one person: canonical storage + a unique index that actually works.

THE BUG. A number already registered could be registered again. Measured on
production:

    uq_users_phone_digits   valid=False  ready=False  unique=True

The index is INVALID. `CREATE INDEX CONCURRENTLY` builds in two passes and, if
the validation pass finds duplicates, it aborts and *leaves the index behind*
marked `indisvalid = false`. Postgres then silently ignores it — it neither
enforces uniqueness nor uses it for lookups — while `\\d users` and `pg_indexes`
both still show a healthy-looking UNIQUE index. So platform-wide phone
uniqueness was declared in the model, created by r72, visible in the schema,
and enforcing nothing. Two duplicate pairs existed, one of them registered the
day before this migration was written.

TWO INDEPENDENT DEFECTS, both fixed here:

1. THE DEAD INDEX. Rebuilt non-concurrently (the table is tiny, so the brief
   lock costs nothing and a transactional build cannot leave debris behind).
   Rebuilding is only possible once the duplicates are gone, hence the data
   step below.

2. RAW-STRING UNIQUENESS on the customer/guarantor side.
   `uq_customer_tenant_phone` was a plain constraint over the raw column, so
   ``+998 90 123 45 67`` and ``998901234567`` were different values and the
   same person could be entered twice in one shop. Phones are now canonicalised
   to ``+998XXXXXXXXX`` on write (app/core/validators.py) AND the constraints
   become functional indexes over digits — belt and braces, because a path that
   bypasses the Pydantic validator (an import, a bot handler building the model
   directly) must not be able to defeat the rule.

   The index NAMES are deliberately unchanged so `_INTEGRITY_CODES` in
   app/main.py keeps mapping them to their user-facing 409 codes.

THE DATA STEP. Two pairs of real accounts shared a number. Which account keeps
it is a business decision, not a technical one, and it was made by the operator:
the phone is cleared on the *inactive* member of each pair. Nothing is deleted
and no account is deactivated — they simply lose the phone field and can set a
different one. Rows are matched by `telegram_id`, which is stable and
meaningful, and the statement is a no-op on any database that lacks them (a
fresh install, CI, the test database).

If duplicates somehow remain after that, the migration RAISES with the offending
numbers listed rather than attempting an index build that would fail — which is
precisely how the invalid index came to exist in the first place.

Revision ID: r96phoneuniq
Revises: r95globalrls
"""
from __future__ import annotations

from alembic import op

revision = "r96phoneuniq"
down_revision = "r95globalrls"
branch_labels = None
depends_on = None

#: Every column holding a phone people type in. `sms_messages.phone` is
#: deliberately excluded: it is a delivery log of what was actually sent, and
#: rewriting it would falsify history for no benefit.
PHONE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("users", "phone"),
    ("customers", "phone"),
    ("customers", "secondary_phone"),
    ("guarantors", "phone"),
    ("tenants", "contact_phone"),
    ("tenant_settings", "contact_phone"),
)

#: Telegram IDs of the accounts that give up the shared number. Chosen by the
#: operator: in each pair the account with no shop and negligible activity.
#:   7890576743 = @alikh8007      (the active owner @x_alisher8007 keeps it)
#:   1234567890 = @MuddatoSupport (the super-admin @example_admin keeps it)
RELEASE_PHONE_TELEGRAM_IDS: tuple[int, ...] = (7890576743, 1234567890)

#: Mirrors app/services/sms_eskiz.py::canonical_uz_phone. A value that is not a
#: recognisable UZ mobile is LEFT ALONE rather than blanked — this migration
#: does not get to decide that someone's stored data is garbage.
_CANON_FN = """
CREATE OR REPLACE FUNCTION pg_temp._canon_uz_phone(v text) RETURNS text AS $$
DECLARE d text;
BEGIN
    IF v IS NULL OR btrim(v) = '' THEN RETURN v; END IF;
    d := regexp_replace(v, '[^0-9]', '', 'g');
    IF length(d) = 9 THEN d := '998' || d; END IF;
    IF length(d) = 12 AND d LIKE '998%' THEN RETURN '+' || d; END IF;
    RETURN v;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
"""

#: The index key. NOT plain digits: a bare 9-digit local number and its 998…
#: form are the same phone, and indexing raw digits lets ``901112233`` in
#: alongside ``+998 90 111 22 33`` — verified, it inserts cleanly. The country
#: code is therefore applied inside the index expression, so the database
#: enforces the rule even for a path that never sees the Pydantic validator (a
#: bot handler, a bulk import, a model built by hand). Every operator here is
#: IMMUTABLE, which is what makes it indexable.
_DIGITS = (
    "CASE WHEN length(regexp_replace({col}, '[^0-9]', '', 'g')) = 9 "
    "THEN '998' || regexp_replace({col}, '[^0-9]', '', 'g') "
    "ELSE regexp_replace({col}, '[^0-9]', '', 'g') END"
)


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    op.execute(_CANON_FN)

    # 1. One stored form everywhere.
    for table, col in PHONE_COLUMNS:
        op.execute(
            f"UPDATE {table} SET {col} = pg_temp._canon_uz_phone({col}) "
            f"WHERE {col} IS NOT NULL AND {col} <> pg_temp._canon_uz_phone({col})"
        )

    # 2. Release the shared number from the accounts the operator chose. Guarded
    #    by the duplicate check below, so this being a no-op elsewhere is safe.
    ids = ", ".join(str(i) for i in RELEASE_PHONE_TELEGRAM_IDS)
    op.execute(
        f"UPDATE users SET phone = NULL WHERE telegram_id IN ({ids}) "
        f"AND phone IS NOT NULL AND EXISTS ("
        f"  SELECT 1 FROM users o WHERE o.id <> users.id AND o.phone IS NOT NULL"
        f"  AND {_DIGITS.format(col='o.phone')} = {_DIGITS.format(col='users.phone')})"
    )

    # 3. Refuse to build an index that would fail — that is how the dead one
    #    was born. Naming the numbers makes the failure actionable.
    op.execute(
        f"""
        DO $$
        DECLARE dupes text;
        BEGIN
            SELECT string_agg(k || ' (x' || n || ')', ', ') INTO dupes FROM (
                SELECT {_DIGITS.format(col='phone')} AS k, count(*) AS n
                FROM users WHERE phone IS NOT NULL
                GROUP BY 1 HAVING count(*) > 1
            ) d;
            IF dupes IS NOT NULL THEN
                RAISE EXCEPTION
                    'cannot enforce phone uniqueness: these numbers are held by '
                    'more than one user: %. Clear the phone on the accounts that '
                    'should give it up, then re-run.', dupes;
            END IF;
        END $$;
        """
    )

    # 4. Replace the DEAD index with a real one. DROP first: an invalid index
    #    still occupies the name, and CREATE would collide with it.
    op.execute("DROP INDEX IF EXISTS uq_users_phone_digits")
    op.execute(
        "CREATE UNIQUE INDEX uq_users_phone_digits ON users "
        f"(({_DIGITS.format(col='phone')})) WHERE phone IS NOT NULL"
    )

    # 5. Customer/guarantor uniqueness becomes format-proof. Same names, so the
    #    409 code mapping in app/main.py is untouched.
    for table, constraint in (
        ("customers", "uq_customer_tenant_phone"),
        ("guarantors", "uq_guarantor_tenant_phone"),
    ):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
        op.execute(f"DROP INDEX IF EXISTS {constraint}")
        op.execute(
            f"CREATE UNIQUE INDEX {constraint} ON {table} "
            f"(tenant_id, ({_DIGITS.format(col='phone')})) WHERE phone IS NOT NULL"
        )


def downgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    for table, constraint in (
        ("customers", "uq_customer_tenant_phone"),
        ("guarantors", "uq_guarantor_tenant_phone"),
    ):
        op.execute(f"DROP INDEX IF EXISTS {constraint}")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} UNIQUE (tenant_id, phone)"
        )
    # The users index is left as the working functional one: restoring a
    # deliberately broken index is not a sane rollback target. Canonicalised
    # values and the released phones are not restored either — both are
    # corrections, and re-introducing duplicates would break the index above.
