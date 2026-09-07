"""Category-linked identifier fields on contract lines (MUDD-85).

Revision ID: rk0catident
Revises: rj0itemcategory
Create Date: 2026-08-28

`catalog_categories.identifier_schema` (r32categoryschema, seeded by
r55categoryschemas) already declares which per-unit fields each category
wants captured at sale time — IMEI 1/2 for phones, VIN/engine number for
cars, and so on — but nothing has ever read it. This adds the table that
holds the values a seller actually enters, one row per (item, key), and
enables it for two reasons at once:

  1. TENANT-WIDE UNIQUENESS. The gate decision (MUDD-94, Example User,
     2026-08-27T18:22:53Z) requires a duplicate IMEI to 400, not just warn —
     unlike the existing `serial_number` "duplicate warning" index on
     `installment_items`. Because `identifier_schema` keys are dynamic per
     category, a single static partial index cannot express that; a child
     table with `UNIQUE(tenant_id, key, value)` can, and does it at the
     database, not just in the API's pre-check.
  2. IMEI 1 becomes REQUIRED for phones. Same gate decision, second and third
     answers: old contracts must be filled in too (enforced at edit, in
     application code — nothing to backfill here, since this table starts
     empty for every existing row) and these are device identifiers, not PII
     (MUDD-55 masking does not apply). r55categoryschemas seeded every
     identifier field, `imei1` included, as `required: false`; this flips
     that flag for exactly the phone categories the decision names.
     `planshetlar` (tablets) also carries an `imei1` entry and is
     deliberately left alone — a Wi-Fi-only tablet has no IMEI at all, so
     requiring one there would block a real, common sale.

INVARIANT: RLS fails closed, using the SAME `public.app_current_tenant()` /
role-scoped policy shape `rf0failclosed_fail_closed_rls.py` put on every
existing tenant table — not the older blanket `current_setting()`-to-PUBLIC
policy that migration retired (`tenant_isolation` is in its `LEGACY_POLICIES`
tuple precisely so nothing reintroduces it). `muddato_app` sees exactly its
own tenant's rows; `muddato_worker` / `muddato_platform` may additionally run
unscoped for background jobs and platform tooling, mirroring `_elevated_pair`
for every other `TENANT_STRICT` table.

This is also the first table created since `rf0failclosed`, so it is the first
to exercise a pattern that migration's own docstring anticipates but does not
yet need: THIS migration carries its OWN frozen `TENANT_STRICT` tuple, for
exactly the table it adds, and `test_rls_manifest_contract.py` is updated to
compare the manifest against the UNION of both migrations' tuples — the same
approach `test_the_role_names_match_the_migration` already uses for
`rg0credsplit`'s `muddato_webhook` role, for the identical reason: asserting
equality against `rf0` alone would fail forever, and rewriting a shipped
migration is not the alternative.

Rows are safe to lose: they are re-derivable from re-entering the sale's
identifiers, and nothing else references this table.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "rk0catident"
down_revision = "rj0itemcategory"
branch_labels = None
depends_on = None

#: This migration's frozen contribution to the `TENANT_STRICT` classification
#: — see `test_rls_manifest_contract.py` for how it is unioned with rf0's.
TENANT_STRICT: tuple[str, ...] = ("installment_item_identifiers",)

_TABLE = "installment_item_identifiers"
_ROLE_APP = "muddato_app"
_ROLE_WORKER = "muddato_worker"
_ROLE_PLATFORM = "muddato_platform"
_T = "public.app_current_tenant()"

_PHONE_CATEGORY_SLUGS = ("smartfonlar", "tugmali-telefonlar")

#: `identifier_schema` is stored as `json`, not `jsonb` (r32categoryschema),
#: so every write back to the column needs an explicit final cast — Postgres
#: does not implicitly convert `jsonb` to `json` on assignment.
_SET_IMEI1_REQUIRED_SQL = """
    UPDATE catalog_categories
    SET identifier_schema = (
        SELECT COALESCE(jsonb_agg(
            CASE WHEN elem->>'key' = 'imei1'
                 THEN jsonb_set(elem, '{{required}}', 'true'::jsonb)
                 ELSE elem END
        ), '[]'::jsonb)::json
        FROM jsonb_array_elements(identifier_schema::jsonb) AS elem
    )
    WHERE slug IN ({slugs})
"""


def _log(message: str) -> None:
    print(f"[rk0catident] {message}")


def _slug_list_sql() -> str:
    return ", ".join(f"'{slug}'" for slug in _PHONE_CATEGORY_SLUGS)


def _policy(name: str, role: str, *, cmd: str = "ALL",
            using: str | None = None, check: str | None = None) -> None:
    """Emit one permissive policy on `_TABLE`. Same shape as rf0's `_policy` —
    duplicated rather than imported, since a migration must not import
    another migration (each is an immutable snapshot).
    """
    op.execute(f"DROP POLICY IF EXISTS {name} ON {_TABLE}")
    parts = [f"CREATE POLICY {name} ON {_TABLE} FOR {cmd} TO {role}"]
    if using is not None:
        parts.append(f"USING ({using})")
    if check is not None:
        parts.append(f"WITH CHECK ({check})")
    op.execute(" ".join(parts))


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s'")

    op.create_table(
        _TABLE,
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", sa.UUID(as_uuid=True),
                  sa.ForeignKey("installment_items.id", ondelete="CASCADE"), nullable=False),
        # A snapshot of `catalog_categories.identifier_schema[].key` at
        # submission time, not a live reference — see the model docstring.
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("value", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_item_identifiers_tenant", _TABLE, ["tenant_id"])
    op.create_index("ix_item_identifiers_item", _TABLE, ["item_id"])
    # THE ENFORCEMENT — a duplicate IMEI (or any other schema-declared value)
    # cannot be recorded twice under the same field for the same tenant.
    op.create_index(
        "uq_item_identifier_tenant_key_value", _TABLE,
        ["tenant_id", "key", "value"], unique=True,
    )

    # TENANT_STRICT policy shape (rf0failclosed § 4): `muddato_app` scoped to
    # exactly its own tenant; `muddato_worker` / `muddato_platform` additionally
    # allowed to run unscoped (an unset `app.tenant_id` under those roles is
    # deliberate elevated access, not an omission — the same `_elevated()`
    # rule every other TENANT_STRICT table gets).
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    scope = f"tenant_id = {_T}"
    _policy(f"rls_{_TABLE}_app", _ROLE_APP, using=scope, check=scope)
    elevated = f"({_T} IS NULL OR tenant_id = {_T})"
    for role in (_ROLE_WORKER, _ROLE_PLATFORM):
        _policy(f"rls_{_TABLE}_{role.split('_')[1]}", role,
                using=elevated, check=elevated)
    _log(f"{_TABLE} created — category-linked identifier fields, "
         "tenant-unique per (key, value)")

    op.execute(_SET_IMEI1_REQUIRED_SQL.format(slugs=_slug_list_sql()))
    _log(f"imei1 marked required for: {', '.join(_PHONE_CATEGORY_SLUGS)}")


def downgrade() -> None:
    op.execute(
        _SET_IMEI1_REQUIRED_SQL.format(slugs=_slug_list_sql())
        .replace("'true'::jsonb", "'false'::jsonb")
    )
    for name in (
        f"rls_{_TABLE}_app", f"rls_{_TABLE}_worker", f"rls_{_TABLE}_platform",
    ):
        op.execute(f"DROP POLICY IF EXISTS {name} ON {_TABLE}")
    op.drop_index("uq_item_identifier_tenant_key_value", table_name=_TABLE)
    op.drop_index("ix_item_identifiers_item", table_name=_TABLE)
    op.drop_index("ix_item_identifiers_tenant", table_name=_TABLE)
    op.drop_table(_TABLE)
