"""RLS policies for the GLOBAL tables r73 deliberately left alone (prod outage fix).

THE OUTAGE. A platform owner added a receiving card; every tenant's "Obuna"
page kept showing "Hozircha qabul qiluvchi karta yo'q" and left "Balansni
to'ldirish" disabled. The card was in the database the whole time. Measured on
production:

    rls_enabled(platform_settings): True
    policies(platform_settings):    0
    as postgres      -> 1 row
    as muddato_app   -> 0 rows

`r73_rls_tenant_isolation` scopes every table that CARRIES a `tenant_id`
column and deliberately leaves the rest alone — its own docstring says the
global catalog must stay readable ("don't let RLS hide the global catalog").
What nobody accounted for is that the live database has RLS switched ON for
those untouched tables anyway (Supabase does that by default), and **RLS
enabled with no policy denies everything** to a non-superuser.

WHY IT SURVIVED THREE FIX ATTEMPTS. The two verbs fail differently:

  * INSERT raises InsufficientPrivilegeError — loud. That is how the
    `installment_items` outage (r94childrls) was found within the hour.
  * SELECT simply returns **zero rows** — no error, no log, no trace. Every
    layer above behaves correctly on an empty set, so `get_billing_cards`
    returns `[]` and the page truthfully renders "no cards configured".

It is invisible in the test suite, which runs with RLS off, and invisible to
the platform admin, whose requests run as `postgres` and bypass RLS entirely.
Only the tenant path — under the restricted `muddato_app` role — sees nothing.

BLAST RADIUS. 17 tables were in this state. This migration restores r73's
intended posture for the ones the tenant path actually touches, and tightens
writes where ownership is unambiguous:

  * platform_settings — receiving cards, support contact, defaults, reminders
  * plans            — a tenant could not read the plan catalogue it subscribes to
  * catalog_*        — 1143 rows of global brands/categories/colours, invisible
  * holidays         — instalment date scheduling read `select`s these
  * promo_codes, ad_campaigns — platform-owned, tenant-readable
  * tenants          — a tenant could not read its OWN row. This is also the
                       true cause of the `Could not refresh instance '<Tenant>'`
                       500 patched in tenant_settings.py: the post-commit
                       refresh SELECTs `tenants` and got back nothing.
  * users            — staff lists, customer→user links and Telegram identity
                       lookups all silently resolved to empty
  * broadcast_recipients — a CHILD of the (policed) `broadcasts` table, written
                       on the tenant path; same shape as r94childrls
  * customer_risk_signals, referrals, sequence_counters — unscoped by design

DELIBERATELY LEFT DENIED: `cron_locks`, `apscheduler_jobs`, `alembic_version`.
Only the scheduler and migrations touch them and both run as `postgres`, so
keeping them unreachable from a tenant request is a real hardening, not an
oversight. If a tenant path ever needs them it will fail loudly on write.

IDEMPOTENT on purpose, exactly as r94childrls: the live database already has
RLS enabled here while a fresh one does not, so `ENABLE` is unconditional (a
no-op when already on) and each policy is dropped-if-exists before creation.

Revision ID: r95globalrls
Revises: r94childrls
"""
from __future__ import annotations

from alembic import op

revision = "r95globalrls"
down_revision = "r94childrls"
branch_labels = None
depends_on = None

#: "There is no tenant scope on this request" — the platform, scheduler and
#: webhook paths never call resolve_tenant. r73's fail-open contract; reused
#: verbatim so all four migrations agree on what "platform path" means.
_NO_SCOPE = "NULLIF(current_setting('app.tenant_id', true), '') IS NULL"
_SCOPE = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

#: Global reference data: every tenant READS it, only the platform WRITES it.
#: The write restriction is stronger than the pre-RLS status quo — a tenant
#: request now cannot alter the shared catalogue or the platform's own
#: settings even if a handler forgets to check, which is the whole point of
#: having RLS at all.
GLOBAL_READ_ONLY: tuple[str, ...] = (
    "platform_settings", "plans", "catalog_brands", "catalog_categories",
    "catalog_colors", "holidays", "promo_codes", "ad_campaigns",
)

#: Unscoped by design — no tenant column exists and none would be correct.
#: `users` spans tenants (one person may be staff at one shop and a customer at
#: another); `customer_risk_signals` is the cross-tenant risk network, whose
#: entire purpose is that a shop sees signals raised by OTHER shops. These
#: match r73's stance for every table it omitted: isolation for these lives in
#: the API layer, not the database.
SHARED_UNSCOPED: tuple[str, ...] = ("users", "referrals", "sequence_counters")


def _global_policy(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (true) WITH CHECK ({_NO_SCOPE})"
    )


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s'")

    for table in GLOBAL_READ_ONLY:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        _global_policy(table)

    for table in SHARED_UNSCOPED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (true) WITH CHECK (true)"
        )

    # A tenant sees and edits its OWN row and no other. `tenants.id` IS the
    # tenant id, so the scope column is the primary key. Creating a tenant
    # happens on the onboarding/platform path, which has no scope and is
    # therefore permitted by the first branch.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON tenants")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenants "
        f"USING ({_NO_SCOPE} OR id = {_SCOPE}) "
        f"WITH CHECK ({_NO_SCOPE} OR id = {_SCOPE})"
    )

    # Reads stay open across tenants — that IS the risk network — but a shop
    # may only raise a signal in its own name.
    op.execute("ALTER TABLE customer_risk_signals ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON customer_risk_signals")
    op.execute(
        "CREATE POLICY tenant_isolation ON customer_risk_signals "
        "USING (true) WITH CHECK ("
        f"{_NO_SCOPE} OR source_tenant_id IS NULL OR source_tenant_id = {_SCOPE})"
    )

    # Child of `broadcasts`: tenancy is reached through the parent, exactly as
    # r94childrls does for the instalment children.
    _via_parent = (
        f"{_NO_SCOPE} OR EXISTS (SELECT 1 FROM broadcasts b "
        f"WHERE b.id = broadcast_id AND b.tenant_id = {_SCOPE})"
    )
    op.execute("ALTER TABLE broadcast_recipients ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON broadcast_recipients")
    op.execute(
        "CREATE POLICY tenant_isolation ON broadcast_recipients "
        f"USING ({_via_parent}) WITH CHECK ({_via_parent})"
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    for table in (
        *GLOBAL_READ_ONLY, *SHARED_UNSCOPED,
        "tenants", "customer_risk_signals", "broadcast_recipients",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        # Left ENABLED: that is how the live database arrived here, and
        # disabling would silently widen access beyond what r73 chose.
