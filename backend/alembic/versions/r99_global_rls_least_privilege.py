"""Least-privilege policies for sensitive global tables.

Revision ID: r99globalrls
Revises: r98notifdelivery
"""
from alembic import op

revision = "r99globalrls"
down_revision = "r98notifdelivery"
branch_labels = None
depends_on = None

_NO_SCOPE = "NULLIF(current_setting('app.tenant_id', true), '') IS NULL"
_SCOPE = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_PUBLIC_SETTING_KEYS = (
    "billing_cards",
    "payment_terms",
    "referral",
    "business_types",
    "how_found_options",
    "demo_plan",
)


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s'")

    keys = ", ".join(f"'{key}'" for key in _PUBLIC_SETTING_KEYS)
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON platform_settings")
    op.execute("DROP POLICY IF EXISTS platform_settings_select ON platform_settings")
    op.execute("DROP POLICY IF EXISTS platform_settings_write ON platform_settings")
    op.execute(
        "CREATE POLICY platform_settings_select ON platform_settings FOR SELECT "
        f"USING ({_NO_SCOPE} OR key IN ({keys}))"
    )
    op.execute(
        "CREATE POLICY platform_settings_write ON platform_settings FOR ALL "
        f"USING ({_NO_SCOPE}) WITH CHECK ({_NO_SCOPE})"
    )

    # A scoped request may see people related to its shop, never the platform's
    # full user directory. Auth/platform paths have no tenant scope and retain
    # their existing access.
    user_visible = (
        f"{_NO_SCOPE} OR EXISTS (SELECT 1 FROM tenant_users tu "
        f"WHERE tu.user_id = users.id AND tu.tenant_id = {_SCOPE}) "
        f"OR EXISTS (SELECT 1 FROM customers c WHERE c.telegram_id = users.telegram_id "
        f"AND c.tenant_id = {_SCOPE})"
    )
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON users")
    op.execute("DROP POLICY IF EXISTS users_select ON users")
    op.execute("DROP POLICY IF EXISTS users_write ON users")
    op.execute(f"CREATE POLICY users_select ON users FOR SELECT USING ({user_visible})")
    op.execute(
        "CREATE POLICY users_write ON users FOR ALL "
        f"USING ({_NO_SCOPE}) WITH CHECK ({_NO_SCOPE})"
    )

    referral_visible = (
        f"{_NO_SCOPE} OR referee_tenant_id = {_SCOPE} OR EXISTS ("
        f"SELECT 1 FROM tenant_users tu WHERE tu.user_id = referrals.referrer_user_id "
        f"AND tu.tenant_id = {_SCOPE})"
    )
    referral_write = f"{_NO_SCOPE} OR referee_tenant_id = {_SCOPE}"
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON referrals")
    op.execute("DROP POLICY IF EXISTS referrals_select ON referrals")
    op.execute("DROP POLICY IF EXISTS referrals_write ON referrals")
    op.execute(
        f"CREATE POLICY referrals_select ON referrals FOR SELECT USING ({referral_visible})"
    )
    op.execute(
        "CREATE POLICY referrals_write ON referrals FOR ALL "
        f"USING ({referral_write}) WITH CHECK ({referral_write})"
    )

    # Tenant requests must not inspect or rewrite the legal sequence table.
    # The one required mutation is exposed as a narrow SECURITY DEFINER function.
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON sequence_counters")
    op.execute("DROP POLICY IF EXISTS sequence_counters_platform_only ON sequence_counters")
    op.execute(
        "CREATE POLICY sequence_counters_platform_only ON sequence_counters "
        f"USING ({_NO_SCOPE}) WITH CHECK ({_NO_SCOPE})"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.next_invoice_sequence(p_year integer)
        RETURNS bigint
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE next_value bigint;
        BEGIN
          INSERT INTO public.sequence_counters (name, value)
          VALUES ('invoice:' || p_year::text, 1)
          ON CONFLICT (name) DO UPDATE
            SET value = public.sequence_counters.value + 1
          RETURNING value INTO next_value;
          RETURN next_value;
        END;
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.next_invoice_sequence(integer) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.next_invoice_sequence(integer) TO muddato_app")


def downgrade() -> None:
    op.execute("SET lock_timeout = '5s'")
    op.execute("DROP FUNCTION IF EXISTS public.next_invoice_sequence(integer)")

    for table in ("platform_settings", "users", "referrals", "sequence_counters"):
        for policy in (
            "platform_settings_select", "platform_settings_write",
            "users_select", "users_write", "referrals_select", "referrals_write",
            "sequence_counters_platform_only",
        ):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        if table == "platform_settings":
            op.execute(
                f"CREATE POLICY tenant_isolation ON {table} USING (true) "
                f"WITH CHECK ({_NO_SCOPE})"
            )
        else:
            op.execute(
                f"CREATE POLICY tenant_isolation ON {table} USING (true) WITH CHECK (true)"
            )
