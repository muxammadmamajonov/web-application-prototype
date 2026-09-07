"""Fail-closed row-level security: explicit authority roles, no fail-open branch.

WHAT WAS WRONG
--------------
Measured on a fresh database at head ``re2noticeretry`` (48 policies over 45
RLS-enabled tables):

* **46 of 48 policies granted access when ``app.tenant_id`` was unset.**
  38 carried the explicit ``NULLIF(current_setting('app.tenant_id',true),'')
  IS NULL OR …`` branch inherited from r73; a further 8 were simply
  ``USING (true)``. Only ``document_deliveries`` and ``payment_review_notices``
  (Batch 4) were fail-closed.
* **27 policies additionally granted a tenant every ``tenant_id IS NULL`` row**,
  i.e. the platform's own rows in ``audit_log``, ``invitations``, ``notifications``,
  ``receipt_blobs``, ``media_files`` and ``webhook_dlq``.
* **``cron_locks``, ``apscheduler_jobs`` and ``alembic_version`` were fully
  readable AND writable by the restricted role.** r95's docstring claims they
  were "deliberately left denied"; they were not. RLS is DISABLED on them and
  r73 granted ``SELECT, INSERT, UPDATE, DELETE ON ALL TABLES`` to
  ``muddato_app``, and *disabled RLS plus a grant is unrestricted access*. A
  tenant request could delete the scheduler's lease row or rewrite the Alembic
  version stamp. Verified:
  ``has_table_privilege('muddato_app','cron_locks','INSERT') = true``.

Together these meant the isolation boundary held only while every route
remembered to call ``resolve_tenant``. Forgetting it did not deny — it granted
*everything*, because the connection role is the table owner and the policy's
first branch waves through an unscoped transaction.

WHAT THIS MIGRATION DOES
------------------------
1. **Authority becomes a role, not the absence of information.** Five NOLOGIN,
   NOSUPERUSER, NOBYPASSRLS roles that own no table:

   =================  ==========================================================
   ``muddato_app``     DEFAULT for every session — tenant work + self identity
   ``muddato_customer``a phone-verified customer, across the shops they buy from
   ``muddato_identity``the pre-authentication identity bootstrap (login, bot)
   ``muddato_worker``  scheduler / bot / webhook / cron
   ``muddato_platform``platform HQ
   =================  ==========================================================

   They share DML grants through the ``muddato_runtime`` group role, so grants
   are managed in one place and infrastructure tables can be revoked from all of
   them at once.

2. **Every policy names the role it serves** (``TO muddato_app`` etc.). A table
   with no policy for the current role denies — the default is deny, not allow.

3. **No policy has a "no scope ⇒ all rows" branch.** ``app_current_tenant()``
   returns NULL for an unset, blank *or malformed* GUC (the regex guard means a
   junk value yields NULL instead of raising 22P02), and ``tenant_id = NULL`` is
   NULL, so the row is filtered and an INSERT is refused. Unset, blank and
   malformed are therefore the same answer: nothing.

4. **``tenant_id IS NULL`` no longer means "everyone".** Platform-owned rows are
   reachable only through ``muddato_platform``/``muddato_worker``. The one
   deliberate exception is ``catalog_models``, where NULL is the shared product
   catalogue: tenants READ it and cannot write it.

5. **Two narrow SECURITY DEFINER capability functions** replace the old
   "unscoped ⇒ omniscient" bootstrap for the paths that legitimately have to
   find a tenant before they know one — a signed download link and an invitation
   credential. Each takes a value the caller has already proved it holds and
   returns an **identifier only**, never row data.

FORCE ROW LEVEL SECURITY — the explicit decision
------------------------------------------------
NOT applied, on any category. The schema owner must stay able to run backfills
(r61, r69, ra0, rb0, rc0 all rewrite rows), and FORCE would make every future
data migration fail against its own tables. The property FORCE would have bought
— "runtime cannot bypass via ownership" — is instead guaranteed on the runtime
side, where it is testable: ``app.db`` issues ``SET LOCAL ROLE`` on *every*
transaction rather than only when a route remembers, ``app.cli.rls_preflight``
exits non-zero if the effective runtime role owns a protected table, and
``tests/test_rls_matrix.py`` asserts each authority role is NOSUPERUSER,
NOBYPASSRLS and owns nothing.

DOWNGRADE RESTORES A VULNERABILITY
-----------------------------------
``downgrade()`` puts the r73/r95/r99 fail-open policies back, because that is
what "reverse this migration" honestly means. Running it re-opens every table to
an unscoped transaction and re-grants ``cron_locks``/``apscheduler_jobs``/
``alembic_version`` to the restricted role. It exists for a schema-level rollback
of the whole deploy; it is not a safe operational step on its own.

LOCK BEHAVIOUR
--------------
``ALTER TABLE … ENABLE ROW LEVEL SECURITY``, ``CREATE POLICY`` and ``DROP POLICY``
each take a brief ACCESS EXCLUSIVE lock on the table and hold it only for the
catalogue update — no table rewrite, no scan, no data movement. ``lock_timeout``
is bounded at 5s so the deploy fails fast behind a long-running query instead of
queueing and stalling traffic. ``transaction_per_migration`` keeps the whole
change atomic: either every policy is the new shape or none is.

Revision ID: rf0failclosed
Revises: re2noticeretry
"""
from __future__ import annotations

from alembic import op

revision = "rf0failclosed"
down_revision = "re2noticeretry"
branch_labels = None
depends_on = None

# ─────────────────────────────────────────────────────────────────────────────
# The manifest, frozen. A migration is an immutable snapshot, so it never
# imports app.core.rls_manifest; tests/test_rls_manifest_contract.py asserts the
# two agree, which is what stops them drifting.
# ─────────────────────────────────────────────────────────────────────────────

ROLE_RUNTIME = "muddato_runtime"
ROLE_APP = "muddato_app"
ROLE_CUSTOMER = "muddato_customer"
ROLE_IDENTITY = "muddato_identity"
ROLE_WORKER = "muddato_worker"
ROLE_PLATFORM = "muddato_platform"

AUTHORITY_ROLES = (ROLE_APP, ROLE_CUSTOMER, ROLE_IDENTITY, ROLE_WORKER, ROLE_PLATFORM)

TENANT_STRICT: tuple[str, ...] = (
    "audit_log", "broadcasts", "consent_records", "customers",
    "document_deliveries", "guarantors", "installments", "invitations",
    "invoices", "kyc_blobs", "media_files", "notification_channels",
    "notifications", "payment_review_notices", "payments", "products",
    "receipt_blobs", "sms_messages", "stock_movements", "tenant_balance_txns",
    "tenant_payment_providers", "tenant_roles", "tenant_settings",
    "tenant_staff_invitations", "tenant_subscriptions", "tenant_topups",
)
TENANT_SELF: tuple[str, ...] = ("tenant_users",)
TENANT_GLOBAL_READ: tuple[str, ...] = ("catalog_models",)
TENANT_CHILD: tuple[tuple[str, str, str], ...] = (
    ("installment_items", "installments", "installment_id"),
    ("installment_guarantors", "installments", "installment_id"),
    ("broadcast_recipients", "broadcasts", "broadcast_id"),
)
IDENTITY_TABLES: tuple[str, ...] = ("users", "tenants")
GLOBAL_READ: tuple[str, ...] = (
    "plans", "catalog_brands", "catalog_categories", "catalog_colors",
    "holidays", "promo_codes", "ad_campaigns",
)
PLATFORM_ONLY: tuple[str, ...] = ("sequence_counters", "referrals", "webhook_dlq")
WORKER_ONLY: tuple[str, ...] = ("cron_locks", "apscheduler_jobs")
MIGRATION_ONLY: tuple[str, ...] = ("alembic_version",)

PUBLIC_SETTING_KEYS: tuple[str, ...] = (
    "billing_cards", "payment_terms", "referral",
    "business_types", "how_found_options", "demo_plan",
)

#: Policy names created before this migration, dropped wholesale so no legacy
#: permissive policy survives to OR itself back into the result.
LEGACY_POLICIES: tuple[str, ...] = (
    "tenant_isolation",
    "platform_settings_select", "platform_settings_write",
    "users_select", "users_write",
    "referrals_select", "referrals_write",
    "sequence_counters_platform_only",
)

# ─────────────────────────────────────────────────────────────────────────────
# Policy expression fragments
# ─────────────────────────────────────────────────────────────────────────────

T = "public.app_current_tenant()"
U = "public.app_current_user()"
TG = "public.app_verified_telegram_id()"
INV = "public.app_current_invitation()"

#: A worker/platform session may narrow itself to one tenant by setting the GUC.
#: Unset means "this role is deliberately running unscoped", which is authority
#: carried by the ROLE, not inferred from missing information.
def _elevated(col: str = "tenant_id") -> str:
    return f"({T} IS NULL OR {col} = {T})"


def _customer_owns_customer(col: str = "customer_id") -> str:
    return (
        f"EXISTS (SELECT 1 FROM public.customers c "
        f"WHERE c.id = {col} AND c.telegram_id IS NOT NULL AND c.telegram_id = {TG})"
    )


def _customer_owns_installment(col: str) -> str:
    return (
        f"EXISTS (SELECT 1 FROM public.installments i "
        f"JOIN public.customers c ON c.id = i.customer_id "
        f"WHERE i.id = {col} AND c.telegram_id IS NOT NULL AND c.telegram_id = {TG})"
    )


def _customer_shop(col: str = "tenant_id") -> str:
    return (
        f"EXISTS (SELECT 1 FROM public.customers c "
        f"WHERE c.tenant_id = {col} AND c.telegram_id IS NOT NULL AND c.telegram_id = {TG})"
    )


def _policy(name: str, table: str, role: str, *, cmd: str = "ALL",
            using: str | None = None, check: str | None = None) -> None:
    """Emit one permissive policy, dropping any same-named predecessor first."""
    op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    parts = [f"CREATE POLICY {name} ON {table} FOR {cmd} TO {role}"]
    if using is not None:
        parts.append(f"USING ({using})")
    if check is not None:
        parts.append(f"WITH CHECK ({check})")
    op.execute(" ".join(parts))


def _reset(table: str) -> None:
    """Enable RLS and remove every policy the table currently carries."""
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    for legacy in LEGACY_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {legacy} ON {table}")


def _elevated_pair(table: str) -> None:
    """Worker + platform access to a tenant-columned table."""
    for role in (ROLE_WORKER, ROLE_PLATFORM):
        _policy(f"rls_{table}_{role.split('_')[1]}", table, role,
                using=_elevated(), check=_elevated())


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s'")

    # ── 1. Scope accessors ────────────────────────────────────────────────
    # Pure-SQL and STABLE so the planner can inline them into the policy qual.
    # The regex is the fail-closed guard: an unset, blank or MALFORMED GUC
    # yields NULL rather than raising 22P02 on the cast, so a junk header can
    # never turn into an unhandled 500 and never widens the result set.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_current_tenant() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $$
          SELECT CASE WHEN current_setting('app.tenant_id', true) ~*
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            THEN current_setting('app.tenant_id', true)::uuid END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_current_user() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $$
          SELECT CASE WHEN current_setting('app.user_id', true) ~*
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            THEN current_setting('app.user_id', true)::uuid END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_current_invitation() RETURNS uuid
        LANGUAGE sql STABLE PARALLEL SAFE
        SET search_path = pg_catalog, public
        AS $$
          SELECT CASE WHEN current_setting('app.invitation_id', true) ~*
              '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            THEN current_setting('app.invitation_id', true)::uuid END
        $$
        """
    )
    # The customer link, resolved once per query. SECURITY DEFINER because the
    # customer policies below must not recurse into `users`' own policy; it
    # returns a single bigint and only for an account that has PROVED it owns an
    # eligible number, so an unverified session gets NULL and therefore no rows.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.app_verified_telegram_id() RETURNS bigint
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT u.telegram_id FROM public.users u
           WHERE u.id = public.app_current_user()
             AND u.verified_phone IS NOT NULL
             AND u.phone_eligibility = 'ELIGIBLE'
        $$
        """
    )

    # ── 2. Capability resolvers ───────────────────────────────────────────
    # Each takes something the caller has already proved it holds (a valid HMAC
    # signature over the object id; the raw invitation credential) and returns
    # an IDENTIFIER, never row data. They exist so a capability request can pin
    # a scope instead of running unscoped and omniscient.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.rls_owning_tenant(p_kind text, p_id uuid)
        RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT CASE p_kind
            WHEN 'contract'        THEN (SELECT tenant_id FROM public.installments   WHERE id = p_id)
            WHEN 'installment'     THEN (SELECT tenant_id FROM public.installments   WHERE id = p_id)
            WHEN 'payment'         THEN (SELECT tenant_id FROM public.payments       WHERE id = p_id)
            WHEN 'payment_receipt' THEN (SELECT tenant_id FROM public.payments       WHERE id = p_id)
            WHEN 'invoice'         THEN (SELECT tenant_id FROM public.invoices       WHERE id = p_id)
            WHEN 'receipt_blob'    THEN (SELECT tenant_id FROM public.receipt_blobs  WHERE id = p_id)
            WHEN 'media_file'      THEN (SELECT tenant_id FROM public.media_files    WHERE id = p_id)
            ELSE NULL END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.rls_resolve_invitation(
            p_verifier text, p_legacy text[])
        RETURNS TABLE (invitation_id uuid, tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT i.id, i.tenant_id FROM public.invitations i
           WHERE (p_verifier IS NOT NULL AND i.token_verifier = p_verifier)
              OR (p_legacy IS NOT NULL AND i.token = ANY (p_legacy))
           LIMIT 1
        $$
        """
    )

    # ── 3. Roles and grants ───────────────────────────────────────────────
    for role in (ROLE_RUNTIME, *AUTHORITY_ROLES):
        op.execute(
            "DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
            "NOCREATEROLE INHERIT; "
            "END IF; END $$;"
        )
        # INHERIT is load-bearing: the DML grants live on `muddato_runtime` and
        # reach the authority roles only through membership. `muddato_app` was
        # created by r73 and can be NOINHERIT in a cluster that predates this
        # migration, which would leave it with schema access and no table access
        # at all. LOGIN/INHERIT need only CREATEROLE, so attempt them always.
        op.execute(
            "DO $$ BEGIN BEGIN "
            f"EXECUTE 'ALTER ROLE {role} NOLOGIN INHERIT'; "
            "EXCEPTION WHEN insufficient_privilege THEN "
            f"RAISE WARNING 'rf0failclosed: cannot set NOLOGIN/INHERIT on {role} "
            "(%)', SQLERRM; END; END $$;"
        )
        # SUPERUSER/BYPASSRLS can only be changed by a superuser, and the
        # deployment migration role is deliberately not one (Supabase's
        # `postgres` owns the schema without being super). Attempt it only when
        # the role actually carries the attribute; a migration that cannot fix it
        # WARNS, and `app.cli.rls_preflight` is what refuses to let production
        # run in that posture — enforcement lives where it can be enforced.
        op.execute(
            "DO $$ BEGIN "
            f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}' "
            "AND (rolsuper OR rolbypassrls)) THEN "
            "BEGIN "
            f"EXECUTE 'ALTER ROLE {role} NOSUPERUSER NOBYPASSRLS'; "
            "EXCEPTION WHEN insufficient_privilege THEN "
            f"RAISE WARNING 'rf0failclosed: cannot normalise role {role} (%); "
            "run it as a superuser before enabling RLS', SQLERRM; "
            "END; END IF; END $$;"
        )

    for role in AUTHORITY_ROLES:
        # Re-grant so the membership picks up the (now guaranteed) INHERIT
        # attribute: on PG16+ the inherit option is frozen into the membership
        # row at GRANT time, so a grant made while the role was NOINHERIT stays
        # non-inheriting even after ALTER ROLE … INHERIT.
        op.execute(f"REVOKE {ROLE_RUNTIME} FROM {role}")
        op.execute(f"GRANT {ROLE_RUNTIME} TO {role}")
        # The connection role must be able to assume each authority. Granting a
        # role you do not hold ADMIN OPTION on fails; warn rather than wedge the
        # deploy, and let the preflight refuse to serve traffic.
        op.execute(
            "DO $$ BEGIN BEGIN "
            f"EXECUTE 'GRANT {role} TO CURRENT_USER'; "
            "EXCEPTION WHEN insufficient_privilege THEN "
            f"RAISE WARNING 'rf0failclosed: cannot GRANT {role} to the migration "
            "role (%); grant it out of band', SQLERRM; END; END $$;"
        )

    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE_RUNTIME}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE_RUNTIME}"
    )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE_RUNTIME}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {ROLE_RUNTIME}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {ROLE_RUNTIME}"
    )

    # r73 granted DML directly to muddato_app. Withdraw it so the group role is
    # the single place grants are managed and a revoke there is effective.
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE_APP}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE_APP}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {ROLE_APP}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {ROLE_APP}"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE_APP}")

    # Function privileges: PUBLIC EXECUTE on a SECURITY DEFINER function is a
    # standing invitation, so narrow it to the runtime group.
    for fn in (
        "public.app_current_tenant()", "public.app_current_user()",
        "public.app_current_invitation()", "public.app_verified_telegram_id()",
        "public.rls_owning_tenant(text, uuid)",
        "public.rls_resolve_invitation(text, text[])",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {ROLE_RUNTIME}")
    # r99's gapless invoice sequence was granted to muddato_app directly.
    op.execute("REVOKE ALL ON FUNCTION public.next_invoice_sequence(integer) FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.next_invoice_sequence(integer) TO {ROLE_RUNTIME}"
    )

    # ── 4. Strict tenant tables ───────────────────────────────────────────
    scope = f"tenant_id = {T}"
    for t in TENANT_STRICT:
        _reset(t)
        _policy(f"rls_{t}_app", t, ROLE_APP, using=scope, check=scope)
        _elevated_pair(t)

    # ── 5. tenant_users — tenant scope plus the login bootstrap ───────────
    _reset("tenant_users")
    _policy("rls_tenant_users_app_select", "tenant_users", ROLE_APP, cmd="SELECT",
            using=f"tenant_id = {T} OR user_id = {U}")
    _policy("rls_tenant_users_app_insert", "tenant_users", ROLE_APP, cmd="INSERT",
            check=scope)
    _policy("rls_tenant_users_app_update", "tenant_users", ROLE_APP, cmd="UPDATE",
            using=scope, check=scope)
    _policy("rls_tenant_users_app_delete", "tenant_users", ROLE_APP, cmd="DELETE",
            using=scope)
    _policy("rls_tenant_users_identity", "tenant_users", ROLE_IDENTITY, cmd="SELECT",
            using=f"user_id = {U}")
    _elevated_pair("tenant_users")

    # ── 6. catalog_models — own rows plus the shared catalogue ────────────
    _reset("catalog_models")
    _policy("rls_catalog_models_app_select", "catalog_models", ROLE_APP, cmd="SELECT",
            using=f"tenant_id IS NULL OR tenant_id = {T}")
    _policy("rls_catalog_models_app_insert", "catalog_models", ROLE_APP, cmd="INSERT",
            check=scope)
    _policy("rls_catalog_models_app_update", "catalog_models", ROLE_APP, cmd="UPDATE",
            using=scope, check=scope)
    _policy("rls_catalog_models_app_delete", "catalog_models", ROLE_APP, cmd="DELETE",
            using=scope)
    _policy("rls_catalog_models_customer", "catalog_models", ROLE_CUSTOMER, cmd="SELECT",
            using="tenant_id IS NULL")
    _elevated_pair("catalog_models")

    # ── 7. Children — tenancy through the parent row ──────────────────────
    for child, parent, fk in TENANT_CHILD:
        via = (
            f"EXISTS (SELECT 1 FROM public.{parent} p "
            f"WHERE p.id = {child}.{fk} AND p.tenant_id = {T})"
        )
        elev = (
            f"{T} IS NULL OR EXISTS (SELECT 1 FROM public.{parent} p "
            f"WHERE p.id = {child}.{fk} AND p.tenant_id = {T})"
        )
        _reset(child)
        _policy(f"rls_{child}_app", child, ROLE_APP, using=via, check=via)
        for role in (ROLE_WORKER, ROLE_PLATFORM):
            _policy(f"rls_{child}_{role.split('_')[1]}", child, role,
                    using=elev, check=elev)

    # ── 8. Identity: users ────────────────────────────────────────────────
    # A scoped request may see the people attached to ITS shop and its own
    # account. With no tenant scope the two EXISTS branches are false, so a
    # request that forgot resolve_tenant sees exactly one user: itself.
    _reset("users")
    users_visible = (
        f"id = {U} "
        f"OR EXISTS (SELECT 1 FROM public.tenant_users tu "
        f"WHERE tu.user_id = users.id AND tu.tenant_id = {T}) "
        f"OR EXISTS (SELECT 1 FROM public.customers c "
        f"WHERE c.telegram_id = users.telegram_id AND c.tenant_id = {T})"
    )
    _policy("rls_users_app_select", "users", ROLE_APP, cmd="SELECT", using=users_visible)
    _policy("rls_users_app_update", "users", ROLE_APP, cmd="UPDATE",
            using=f"id = {U}", check=f"id = {U}")
    _policy("rls_users_customer_select", "users", ROLE_CUSTOMER, cmd="SELECT",
            using=f"id = {U}")
    _policy("rls_users_customer_update", "users", ROLE_CUSTOMER, cmd="UPDATE",
            using=f"id = {U}", check=f"id = {U}")
    # The identity bootstrap has to find an account BEFORE anyone is signed in
    # (Telegram callback, OIDC callback, bot /start), so it is the one authority
    # with directory-wide access to `users` — and it can reach nothing else.
    _policy("rls_users_identity", "users", ROLE_IDENTITY, using="true", check="true")
    _policy("rls_users_worker", "users", ROLE_WORKER, using="true", check="true")
    _policy("rls_users_platform", "users", ROLE_PLATFORM, using="true", check="true")

    # ── 9. Identity: tenants ──────────────────────────────────────────────
    _reset("tenants")
    tenants_visible = (
        f"id = {T} "
        f"OR EXISTS (SELECT 1 FROM public.tenant_users tu "
        f"WHERE tu.tenant_id = tenants.id AND tu.user_id = {U} AND tu.is_active)"
    )
    _policy("rls_tenants_app_select", "tenants", ROLE_APP, cmd="SELECT",
            using=tenants_visible)
    _policy("rls_tenants_app_update", "tenants", ROLE_APP, cmd="UPDATE",
            using=f"id = {T}", check=f"id = {T}")
    _policy("rls_tenants_customer_select", "tenants", ROLE_CUSTOMER, cmd="SELECT",
            using=_customer_shop("tenants.id"))
    _policy("rls_tenants_identity_select", "tenants", ROLE_IDENTITY, cmd="SELECT",
            using=f"EXISTS (SELECT 1 FROM public.tenant_users tu "
                  f"WHERE tu.tenant_id = tenants.id AND tu.user_id = {U})")
    _policy("rls_tenants_worker", "tenants", ROLE_WORKER, using="true", check="true")
    _policy("rls_tenants_platform", "tenants", ROLE_PLATFORM, using="true", check="true")

    # ── 10. Platform reference data — every tenant reads, only HQ writes ──
    for t in GLOBAL_READ:
        _reset(t)
        _policy(f"rls_{t}_read", t, ROLE_RUNTIME, cmd="SELECT", using="true")
        _policy(f"rls_{t}_worker", t, ROLE_WORKER, using="true", check="true")
        _policy(f"rls_{t}_platform", t, ROLE_PLATFORM, using="true", check="true")

    # ── 11. platform_settings — allowlisted keys are readable, nothing more ─
    keys = ", ".join(f"'{k}'" for k in PUBLIC_SETTING_KEYS)
    _reset("platform_settings")
    _policy("rls_platform_settings_read", "platform_settings", ROLE_RUNTIME,
            cmd="SELECT", using=f"key IN ({keys})")
    _policy("rls_platform_settings_worker", "platform_settings", ROLE_WORKER,
            using="true", check="true")
    _policy("rls_platform_settings_platform", "platform_settings", ROLE_PLATFORM,
            using="true", check="true")

    # ── 12. The cross-tenant risk network ─────────────────────────────────
    # Reads stay open across tenants — a shop seeing signals raised by OTHER
    # shops IS the product — but a signal may only be raised in the raiser's own
    # name, and an unscoped request may raise none at all.
    _reset("customer_risk_signals")
    _policy("rls_customer_risk_signals_app_select", "customer_risk_signals", ROLE_APP,
            cmd="SELECT", using="true")
    _policy("rls_customer_risk_signals_app_insert", "customer_risk_signals", ROLE_APP,
            cmd="INSERT", check=f"source_tenant_id = {T}")
    _policy("rls_customer_risk_signals_app_update", "customer_risk_signals", ROLE_APP,
            cmd="UPDATE", using=f"source_tenant_id = {T}", check=f"source_tenant_id = {T}")
    _policy("rls_customer_risk_signals_app_delete", "customer_risk_signals", ROLE_APP,
            cmd="DELETE", using=f"source_tenant_id = {T}")
    for role in (ROLE_WORKER, ROLE_PLATFORM):
        _policy(f"rls_customer_risk_signals_{role.split('_')[1]}",
                "customer_risk_signals", role, using="true", check="true")

    # ── 13. Platform-only tables ──────────────────────────────────────────
    for t in PLATFORM_ONLY:
        _reset(t)
        _policy(f"rls_{t}_worker", t, ROLE_WORKER, using="true", check="true")
        _policy(f"rls_{t}_platform", t, ROLE_PLATFORM, using="true", check="true")

    # ── 14. Scheduler infrastructure — the r95 claim, made true ───────────
    # These were fully writable by the restricted role. Revoke the grant AND
    # enable RLS with a worker-only policy, so neither layer alone is load-bearing.
    for t in WORKER_ONLY:
        op.execute(f"REVOKE ALL ON {t} FROM {ROLE_RUNTIME}")
        op.execute(f"REVOKE ALL ON {t} FROM {ROLE_APP}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {ROLE_WORKER}")
        _reset(t)
        _policy(f"rls_{t}_worker", t, ROLE_WORKER, using="true", check="true")
        _policy(f"rls_{t}_platform", t, ROLE_PLATFORM, using="true", check="true")

    # ``alembic_version`` is protected by REVOKE alone, deliberately. Enabling
    # RLS on it means taking ACCESS EXCLUSIVE on the very table Alembic is
    # holding open to stamp this revision, which under a bounded lock_timeout
    # fails the deploy for no security gain: a role with no privilege on a table
    # cannot read or write it whether or not RLS is on.
    for t in MIGRATION_ONLY:
        op.execute(f"REVOKE ALL ON {t} FROM {ROLE_RUNTIME}")
        for role in AUTHORITY_ROLES:
            op.execute(f"REVOKE ALL ON {t} FROM {role}")

    # ── 15. Customer authority — the verified cross-shop reader ───────────
    # Enumerated table by table. Anything not named here is invisible to a
    # customer session, which is the point of enumerating it.
    _policy("rls_customers_customer_select", "customers", ROLE_CUSTOMER, cmd="SELECT",
            using=f"telegram_id IS NOT NULL AND telegram_id = {TG}")
    _policy("rls_installments_customer_select", "installments", ROLE_CUSTOMER,
            cmd="SELECT", using=_customer_owns_customer())
    _policy("rls_installment_items_customer_select", "installment_items", ROLE_CUSTOMER,
            cmd="SELECT", using=_customer_owns_installment("installment_items.installment_id"))
    _policy("rls_payments_customer_select", "payments", ROLE_CUSTOMER, cmd="SELECT",
            using=_customer_owns_installment("payments.installment_id"))
    # A customer initiates their own payment and uploads their own receipt.
    _policy("rls_payments_customer_insert", "payments", ROLE_CUSTOMER, cmd="INSERT",
            check=_customer_owns_installment("payments.installment_id"))
    _policy("rls_payments_customer_update", "payments", ROLE_CUSTOMER, cmd="UPDATE",
            using=_customer_owns_installment("payments.installment_id"),
            check=_customer_owns_installment("payments.installment_id"))
    _policy("rls_notifications_customer_select", "notifications", ROLE_CUSTOMER,
            cmd="SELECT", using=f"user_id = {U}")
    _policy("rls_notifications_customer_update", "notifications", ROLE_CUSTOMER,
            cmd="UPDATE", using=f"user_id = {U}", check=f"user_id = {U}")
    for t in ("tenant_settings", "tenant_payment_providers"):
        _policy(f"rls_{t}_customer_select", t, ROLE_CUSTOMER, cmd="SELECT",
                using=_customer_shop(f"{t}.tenant_id"))
    for t in ("receipt_blobs", "media_files"):
        _policy(f"rls_{t}_customer_select", t, ROLE_CUSTOMER, cmd="SELECT",
                using=_customer_shop(f"{t}.tenant_id"))
        _policy(f"rls_{t}_customer_insert", t, ROLE_CUSTOMER, cmd="INSERT",
                check=_customer_shop(f"{t}.tenant_id"))

    # ── 16. Identity authority — tenant_settings read for the login response ─
    _policy("rls_tenant_settings_identity", "tenant_settings", ROLE_IDENTITY,
            cmd="SELECT",
            using=f"EXISTS (SELECT 1 FROM public.tenant_users tu "
                  f"WHERE tu.tenant_id = tenant_settings.tenant_id AND tu.user_id = {U})")

    # ── 17. Invitation capability — one row, by proven credential ─────────
    # No list access and no enumeration: the GUC holds a single id that the
    # caller obtained by presenting the raw credential to rls_resolve_invitation.
    _policy("rls_invitations_capability", "invitations", ROLE_APP, cmd="SELECT",
            using=f"tenant_id = {T} OR id = {INV}")
    _policy("rls_invitations_capability_update", "invitations", ROLE_APP, cmd="UPDATE",
            using=f"tenant_id = {T} OR id = {INV}",
            check=f"tenant_id = {T} OR id = {INV}")
    _policy("rls_invitations_identity", "invitations", ROLE_IDENTITY, cmd="SELECT",
            using=f"id = {INV}")


def downgrade() -> None:
    """Restore the r73/r95/r99 fail-open posture.

    THIS REINTRODUCES THE VULNERABILITY THIS MIGRATION CLOSED. After running it,
    an unscoped transaction under the restricted role again sees every tenant's
    rows, `tenant_id IS NULL` rows are visible to all tenants, and `cron_locks`,
    `apscheduler_jobs` and `alembic_version` are writable from a tenant request.
    It exists so a whole-deploy schema rollback is possible; it is not a safe
    operational step taken on its own.
    """
    op.execute("SET lock_timeout = '5s'")

    legacy_using = (
        "NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "OR tenant_id IS NULL "
        "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    )
    legacy_check = (
        "NULLIF(current_setting('app.tenant_id', true), '') IS NULL "
        "OR tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
    )
    no_scope = "NULLIF(current_setting('app.tenant_id', true), '') IS NULL"
    scope = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

    all_tables = (
        *TENANT_STRICT, *TENANT_SELF, *TENANT_GLOBAL_READ,
        *(c for c, _p, _f in TENANT_CHILD), *IDENTITY_TABLES, *GLOBAL_READ,
        "platform_settings", "customer_risk_signals",
        *PLATFORM_ONLY, *WORKER_ONLY, *MIGRATION_ONLY,
    )
    for t in all_tables:
        op.execute(
            "DO $$ DECLARE r record; BEGIN "
            f"FOR r IN SELECT policyname FROM pg_policies "
            f"WHERE schemaname='public' AND tablename='{t}' LOOP "
            f"EXECUTE format('DROP POLICY IF EXISTS %I ON public.{t}', r.policyname); "
            "END LOOP; END $$;"
        )

    for t in (*TENANT_STRICT, *TENANT_SELF, *TENANT_GLOBAL_READ):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            f"USING ({legacy_using}) WITH CHECK ({legacy_check})"
        )
    for child, parent, fk in TENANT_CHILD:
        via = (
            f"{no_scope} OR EXISTS (SELECT 1 FROM {parent} p "
            f"WHERE p.id = {child}.{fk} AND p.tenant_id = {scope})"
        )
        op.execute(f"CREATE POLICY tenant_isolation ON {child} "
                   f"USING ({via}) WITH CHECK ({via})")
    for t in GLOBAL_READ:
        op.execute(f"CREATE POLICY tenant_isolation ON {t} "
                   f"USING (true) WITH CHECK ({no_scope})")
    op.execute(
        f"CREATE POLICY tenant_isolation ON tenants "
        f"USING ({no_scope} OR id = {scope}) WITH CHECK ({no_scope} OR id = {scope})"
    )
    op.execute(
        "CREATE POLICY tenant_isolation ON customer_risk_signals USING (true) "
        f"WITH CHECK ({no_scope} OR source_tenant_id IS NULL OR source_tenant_id = {scope})"
    )

    keys = ", ".join(f"'{k}'" for k in PUBLIC_SETTING_KEYS)
    op.execute("CREATE POLICY platform_settings_select ON platform_settings FOR SELECT "
               f"USING ({no_scope} OR key IN ({keys}))")
    op.execute("CREATE POLICY platform_settings_write ON platform_settings FOR ALL "
               f"USING ({no_scope}) WITH CHECK ({no_scope})")
    user_visible = (
        f"{no_scope} OR EXISTS (SELECT 1 FROM tenant_users tu "
        f"WHERE tu.user_id = users.id AND tu.tenant_id = {scope}) "
        f"OR EXISTS (SELECT 1 FROM customers c WHERE c.telegram_id = users.telegram_id "
        f"AND c.tenant_id = {scope})"
    )
    op.execute(f"CREATE POLICY users_select ON users FOR SELECT USING ({user_visible})")
    op.execute("CREATE POLICY users_write ON users FOR ALL "
               f"USING ({no_scope}) WITH CHECK ({no_scope})")
    referral_visible = (
        f"{no_scope} OR referee_tenant_id = {scope} OR EXISTS ("
        f"SELECT 1 FROM tenant_users tu WHERE tu.user_id = referrals.referrer_user_id "
        f"AND tu.tenant_id = {scope})"
    )
    referral_write = f"{no_scope} OR referee_tenant_id = {scope}"
    op.execute(f"CREATE POLICY referrals_select ON referrals FOR SELECT USING ({referral_visible})")
    op.execute("CREATE POLICY referrals_write ON referrals FOR ALL "
               f"USING ({referral_write}) WITH CHECK ({referral_write})")
    op.execute("CREATE POLICY sequence_counters_platform_only ON sequence_counters "
               f"USING ({no_scope}) WITH CHECK ({no_scope})")
    op.execute(f"CREATE POLICY tenant_isolation ON webhook_dlq "
               f"USING ({legacy_using}) WITH CHECK ({legacy_check})")

    for t in WORKER_ONLY:
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")

    # Re-grant directly to muddato_app, as r73 had it.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE_APP}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE_APP}")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
               f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {ROLE_APP}")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
               f"GRANT USAGE, SELECT ON SEQUENCES TO {ROLE_APP}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.next_invoice_sequence(integer) TO {ROLE_APP}")

    op.execute("DROP FUNCTION IF EXISTS public.rls_resolve_invitation(text, text[])")
    op.execute("DROP FUNCTION IF EXISTS public.rls_owning_tenant(text, uuid)")
    op.execute("DROP FUNCTION IF EXISTS public.app_verified_telegram_id()")
    op.execute("DROP FUNCTION IF EXISTS public.app_current_invitation()")
    op.execute("DROP FUNCTION IF EXISTS public.app_current_user()")
    op.execute("DROP FUNCTION IF EXISTS public.app_current_tenant()")

    # The roles are left in place: dropping a role that owns default-privilege
    # ACLs wedges the rollback, and an unused NOLOGIN role grants nothing.
    for role in (ROLE_CUSTOMER, ROLE_IDENTITY, ROLE_WORKER, ROLE_PLATFORM):
        op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                   f"REVOKE ALL ON TABLES FROM {role}")
