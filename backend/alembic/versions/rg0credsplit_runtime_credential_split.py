"""Credential split, webhook bootstrap, identity bootstrap — the Batch 5 correction.

WHAT ``rf0failclosed`` LEFT OPEN
--------------------------------
``rf0failclosed`` made the POLICIES fail closed. It did not make the CONNECTION
fail closed, and an independent review found three consequences.

1. **Ordinary runtime still connected as the schema owner.** ``DATABASE_URL``
   authenticated as the table owner — which, on the managed platform we run on,
   also carries ``BYPASSRLS``. Every transaction downgraded itself with
   ``SET LOCAL ROLE`` from a SQLAlchemy ``after_begin`` listener, so isolation
   held only for work that went through a ``Session``. A health check, a direct
   ``engine.connect()``, APScheduler's own pool, a future CLI or any library
   holding its own connection ran as the owner and row security did not apply
   to it at all. The preflight recorded this as a tolerated "managed-platform
   risk"; it is not tolerable, and it is not necessary.

2. **Public provider webhooks were handed cross-tenant authority at the door.**
   ``worker_authority`` was a *router* dependency on ``/webhooks/click``,
   ``/webhooks/payme`` and ``/webhooks/subscription``, so an unauthenticated
   HTTP request reached the database with cross-tenant permission and performed
   a global ``tenant_settings`` lookup BEFORE any signature was verified.

3. **The pre-authentication identity role could read and write the whole user
   directory.** ``rls_users_identity`` was ``USING (true) WITH CHECK (true)``
   and the authority was attached to the whole ``/auth`` router, so every
   handler under it — including any added later — ran with directory-wide
   ``users`` DML before a credential had been checked.

WHAT THIS MIGRATION DOES
------------------------
**A. A restricted runtime login.** ``muddato_api``: LOGIN, NOSUPERUSER,
NOBYPASSRLS, **NOINHERIT**, owning nothing, with no direct privilege on any
table or sequence and no ``CREATE`` on schema ``public``. It IS granted
``USAGE`` there — see the note at the grant itself; USAGE resolves a name and
grants access to nothing behind it, and having it turns every refusal into a
clean "permission denied" rather than a misleading "relation does not exist".

It is a member of the authority roles, so it may ``SET ROLE`` to them — and
holds none of their privileges until it does. NOINHERIT is the load-bearing
word: an ``INHERIT`` login that is a member of ``muddato_worker`` would silently
satisfy that role's ``USING (true)`` policies on a raw connection, and the split
would exist only on paper. A direct connection under this login can NAME a
protected table and read nothing from it.

The schema owner keeps its job — Alembic, release migrations, operator tools —
under a separate ``MIGRATION_DATABASE_URL``. Neither falls back to the other in
production (``app/config.py``, ``alembic/env.py``).

**B. ``muddato_webhook`` — an authority BELOW ``muddato_app``.** A public
webhook now starts unauthenticated as ``muddato_app`` with no tenant scope
(which reads nothing), assumes ``muddato_webhook`` for exactly one call to a
narrow provider resolver, verifies the provider signature, and only then narrows
to the resolved tenant. ``muddato_webhook`` is deliberately NOT a member of
``muddato_runtime``: it holds **no table privilege at all**, only EXECUTE on the
four resolvers. Each resolver takes the minimal public protocol identifier and
returns one row of exactly what signature verification needs.

The provider secrets these resolvers return are ciphertext, and EXECUTE is
granted to ``muddato_webhook`` alone — never to ``muddato_runtime`` — precisely
so an ordinary tenant handler cannot call ``rls_webhook_click_provider`` with a
competitor's ``service_id`` and read their key.

**C. The identity bootstrap becomes three functions, not a directory.**
``rls_users_identity`` is dropped: ``muddato_identity`` now has NO policy on
``users`` at all, so its direct access is deny-all. What is left is enumerated —
find-or-create by a verified Telegram id, read the phone-proof state of the
Telegram id the caller has just proved it owns, and list candidate bot-token
ciphertexts for initData verification. None accepts ``platform_role``,
``verified_phone``, ``phone_eligibility`` or any other privileged field; none
takes arbitrary filter input; none returns a row the caller did not name with a
credential-derived identifier.

Every function created here is ``SECURITY DEFINER`` with a pinned
``search_path``, owned by the migration owner, ``EXECUTE`` revoked from
``PUBLIC`` and granted to exactly one role.

WHY SECURITY DEFINER AT ALL
---------------------------
Because the caller must be able to reach a row that its own authority cannot
see, using a value it has already proved it holds. That is the definition of a
capability. The alternative — a policy wide enough for the bootstrap — is what
this migration removes. Each function is small enough to read in one screen,
returns identifiers or single columns rather than rows, and is callable by one
role.

LOCK BEHAVIOUR
--------------
``CREATE FUNCTION``, ``DROP POLICY``, ``GRANT`` and ``REVOKE`` touch catalogues
only: no table rewrite, no scan, no data movement. The single table-level lock
is the ``DROP POLICY`` on ``users`` (brief ACCESS EXCLUSIVE, catalogue update
only). ``lock_timeout`` is bounded at 5s so the release fails fast behind a
long-running query instead of queueing behind it and stalling traffic.
``transaction_per_migration`` keeps the whole change atomic.

DOWNGRADE RESTORES TWO AUTHORIZATION DEFECTS
--------------------------------------------
``downgrade()`` puts ``rls_users_identity USING (true) WITH CHECK (true)`` back
and drops the capability functions, which re-opens the entire ``users``
directory to the pre-authentication role and leaves the webhook handlers with no
resolver (they fail closed rather than leaking, but the route stops working). It
also emits a ``WARNING`` saying so while it runs. It exists for a whole-deploy
schema rollback; it is not a safe operational step on its own.

Revision ID: rg0credsplit
Revises: rf0failclosed
"""
from __future__ import annotations

from alembic import op

revision = "rg0credsplit"
down_revision = "rf0failclosed"
branch_labels = None
depends_on = None

# ─────────────────────────────────────────────────────────────────────────────
# Frozen names. A migration is an immutable snapshot, so it never imports
# app.core.rls_manifest; tests/test_rls_manifest_contract.py asserts they agree.
# ─────────────────────────────────────────────────────────────────────────────

ROLE_RUNTIME = "muddato_runtime"
ROLE_APP = "muddato_app"
ROLE_CUSTOMER = "muddato_customer"
ROLE_IDENTITY = "muddato_identity"
ROLE_WEBHOOK = "muddato_webhook"
ROLE_WORKER = "muddato_worker"
ROLE_PLATFORM = "muddato_platform"

#: Every role a request or job may run as, after this migration.
AUTHORITY_ROLES = (
    ROLE_APP, ROLE_CUSTOMER, ROLE_IDENTITY, ROLE_WEBHOOK, ROLE_WORKER, ROLE_PLATFORM,
)

#: The ordinary application LOGIN. Not an authority — an identity that may
#: assume one. Provisioned here so a fresh cluster is correct by construction;
#: its PASSWORD is set out of band and never appears in the repository.
ROLE_API_LOGIN = "muddato_api"


def _try(sql: str, what: str) -> None:
    """Run ``sql``, downgrading an insufficient-privilege failure to a WARNING.

    Role attributes (``NOSUPERUSER``/``NOBYPASSRLS``) and ``CREATE ROLE`` need
    privileges the deployment identity may not hold — Supabase's ``postgres``
    owns the schema without being a superuser. A migration that cannot fix those
    must not wedge the deploy; ``app.cli.rls_preflight`` is what refuses to serve
    traffic in a wrong posture, so enforcement lives where it can be enforced.
    """
    escaped = sql.replace("'", "''")
    op.execute(
        "DO $$ BEGIN BEGIN "
        f"EXECUTE '{escaped}'; "
        "EXCEPTION WHEN insufficient_privilege OR duplicate_object THEN "
        f"RAISE WARNING 'rg0credsplit: could not {what} (%)', SQLERRM; "
        "END; END $$;"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Capability functions. Each is defined once, here, as (name, signature, body,
# grantee) so the create / revoke / grant / drop passes cannot drift apart.
# ─────────────────────────────────────────────────────────────────────────────

#: (identity, signature-for-GRANT, full CREATE statement, role allowed to EXECUTE)
CAPABILITIES: tuple[tuple[str, str, str], ...] = (
    # ── Webhook provider resolution ──────────────────────────────────────────
    (
        "public.rls_webhook_click_provider(text)",
        ROLE_WEBHOOK,
        """
        CREATE OR REPLACE FUNCTION public.rls_webhook_click_provider(p_service_id text)
        RETURNS TABLE (tenant_id uuid, secret_ciphertext text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          SELECT ts.tenant_id, ts.click_secret_key
            FROM public.tenant_settings ts
           WHERE p_service_id IS NOT NULL
             AND p_service_id <> ''
             AND ts.click_service_id = p_service_id
             AND ts.click_secret_key IS NOT NULL
           LIMIT 1
        $fn$
        """,
    ),
    (
        "public.rls_webhook_payme_provider(text)",
        ROLE_WEBHOOK,
        # Keyed on the BLIND INDEX of the presented secret, so producing an
        # argument that matches anything already requires holding the secret.
        # This is not an enumeration surface: an attacker who can compute the
        # blind index has the credential the lookup would have proved.
        """
        CREATE OR REPLACE FUNCTION public.rls_webhook_payme_provider(p_secret_bidx text)
        RETURNS TABLE (tenant_id uuid, secret_ciphertext text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          SELECT ts.tenant_id, ts.payme_secret_key
            FROM public.tenant_settings ts
           WHERE p_secret_bidx IS NOT NULL
             AND p_secret_bidx <> ''
             AND ts.payme_secret_bidx = p_secret_bidx
             AND ts.payme_secret_key IS NOT NULL
           LIMIT 1
        $fn$
        """,
    ),
    (
        "public.rls_webhook_billing_provider(text)",
        ROLE_WEBHOOK,
        # The PLATFORM's own merchant configuration for one named provider —
        # never the whole settings table, never another key. Used to verify the
        # subscription callback before any tenant row is touched.
        """
        CREATE OR REPLACE FUNCTION public.rls_webhook_billing_provider(p_name text)
        RETURNS jsonb
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          SELECT (ps.value -> p_name)
            FROM public.platform_settings ps
           WHERE ps.key = 'billing_providers'
             AND p_name IS NOT NULL
             AND p_name <> ''
           LIMIT 1
        $fn$
        """,
    ),
    (
        "public.rls_webhook_topup_tenant(uuid)",
        ROLE_WEBHOOK,
        # An identifier, not a row: which tenant owns this top-up, so the
        # verified callback can narrow itself before reading or writing it.
        """
        CREATE OR REPLACE FUNCTION public.rls_webhook_topup_tenant(p_topup_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          SELECT t.tenant_id FROM public.tenant_topups t WHERE t.id = p_topup_id
        $fn$
        """,
    ),
    (
        "public.rls_webhook_topup_tenant_by_ref(text)",
        ROLE_WEBHOOK,
        # The Payme subscription protocol identifies a transaction by the
        # provider's own opaque id, which we store on the top-up. Same shape:
        # an identifier out, nothing else.
        """
        CREATE OR REPLACE FUNCTION public.rls_webhook_topup_tenant_by_ref(p_ref text)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          SELECT t.tenant_id FROM public.tenant_topups t
           WHERE p_ref IS NOT NULL AND p_ref <> '' AND t.provider_ref = p_ref
           LIMIT 1
        $fn$
        """,
    ),
    # ── Identity bootstrap ───────────────────────────────────────────────────
    (
        "public.rls_identity_upsert_telegram(bigint, text, text, text, text, text)",
        ROLE_IDENTITY,
        # Find-or-create by a Telegram id the caller has ALREADY proved
        # (validated initData HMAC, or a verified OIDC id_token whose `sub` is
        # the Telegram id). Returns the account id and nothing else.
        #
        # The parameter list is the allowlist. `platform_role`, `is_active`,
        # `verified_phone`, `verified_phone_source`, `phone_eligibility`,
        # `tokens_valid_from`, `ref_code` and `referred_by_user_id` are absent,
        # so no caller can set or change them through this path however the
        # request body is shaped — mass assignment is impossible by signature.
        #
        # `ON CONFLICT (telegram_id)` is what makes two simultaneous first
        # logins produce ONE account: the unique index arbitrates, not a
        # read-then-write race in application code.
        #
        # `locale` is applied only when creating; a returning user keeps the
        # language they chose. Empty strings are treated as absent so an omitted
        # Telegram field cannot blank a stored one.
        """
        CREATE OR REPLACE FUNCTION public.rls_identity_upsert_telegram(
            p_telegram_id bigint,
            p_username    text DEFAULT NULL,
            p_first_name  text DEFAULT NULL,
            p_last_name   text DEFAULT NULL,
            p_photo_url   text DEFAULT NULL,
            p_locale      text DEFAULT NULL)
        RETURNS uuid
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
        DECLARE
            v_id uuid;
        BEGIN
            IF p_telegram_id IS NULL OR p_telegram_id = 0 THEN
                RETURN NULL;
            END IF;
            INSERT INTO public.users AS u (
                id, telegram_id, telegram_username, first_name, last_name,
                photo_url, locale, is_active, last_seen_at)
            VALUES (
                gen_random_uuid(), p_telegram_id,
                nullif(p_username, ''), nullif(p_first_name, ''),
                nullif(p_last_name, ''), nullif(p_photo_url, ''),
                CASE WHEN p_locale IN ('uz', 'ru') THEN p_locale ELSE 'uz' END,
                true, now())
            ON CONFLICT (telegram_id) DO UPDATE SET
                telegram_username = COALESCE(nullif(p_username, ''),   u.telegram_username),
                first_name        = COALESCE(nullif(p_first_name, ''), u.first_name),
                last_name         = COALESCE(nullif(p_last_name, ''),  u.last_name),
                photo_url         = COALESCE(nullif(p_photo_url, ''),  u.photo_url),
                last_seen_at      = now()
            RETURNING u.id INTO v_id;
            RETURN v_id;
        END
        $fn$
        """,
    ),
    (
        "public.rls_identity_user_state(bigint)",
        ROLE_IDENTITY,
        # The phone-proof state of ONE Telegram id — the one the caller has just
        # proved it owns, so everything returned is the caller's own. Five
        # scalars: no name, no email, no birthdate, no platform role, no
        # membership. Enough to render the invitation preview's verification
        # state (including whether the proved number matches the customer the
        # link was minted for) and useless for probing anybody else.
        """
        CREATE OR REPLACE FUNCTION public.rls_identity_user_state(p_telegram_id bigint)
        RETURNS TABLE (user_id uuid, verified boolean, eligibility text,
                       locale text, verified_phone text, known_name text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          SELECT u.id, (u.verified_phone IS NOT NULL), u.phone_eligibility,
                 u.locale, u.verified_phone,
                 -- Mirrors services.display_names.person_name: the self-chosen
                 -- name wins; an operator account contributes nothing (greeting
                 -- a customer with an operator's Telegram name is the defect
                 -- that function exists to prevent); otherwise the Telegram
                 -- profile name. Computed here so the raw columns stay inside.
                 CASE
                   WHEN nullif(btrim(coalesce(u.display_name, '')), '') IS NOT NULL
                     THEN btrim(u.display_name)
                   WHEN u.platform_role IS NOT NULL THEN NULL
                   ELSE nullif(btrim(concat_ws(' ', u.first_name, u.last_name)), '')
                 END
            FROM public.users u
           WHERE p_telegram_id IS NOT NULL AND u.telegram_id = p_telegram_id
        $fn$
        """,
    ),
    (
        "public.rls_identity_bot_token_candidates(integer)",
        ROLE_IDENTITY,
        # Mini-App initData is signed with the bot token of whichever bot opened
        # it, and the payload carries no tenant hint, so verification has to try
        # the configured per-tenant tokens. This returns the CIPHERTEXT column
        # only — no tenant id, no other setting, nothing that says whose token a
        # given row is. Bounded by the caller so a forged initData cannot turn
        # into an unbounded scan.
        """
        CREATE OR REPLACE FUNCTION public.rls_identity_bot_token_candidates(p_limit integer)
        RETURNS SETOF text
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          SELECT ts.bot_token_ciphertext
            FROM public.tenant_settings ts
           WHERE ts.bot_token_ciphertext IS NOT NULL
           ORDER BY ts.tenant_id
           LIMIT GREATEST(0, LEAST(COALESCE(p_limit, 0), 1000))
        $fn$
        """,
    ),
)


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s'")

    # ── 1. The webhook authority ──────────────────────────────────────────
    # NOT a member of muddato_runtime, deliberately: it must hold no table
    # privilege whatsoever, so an unauthenticated request that reaches the
    # database can do exactly one thing — call a resolver — and nothing else.
    op.execute(
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE_WEBHOOK}') THEN "
        f"CREATE ROLE {ROLE_WEBHOOK} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB "
        "NOCREATEROLE INHERIT; "
        "END IF; END $$;"
    )
    _try(f"ALTER ROLE {ROLE_WEBHOOK} NOLOGIN INHERIT",
         f"set NOLOGIN/INHERIT on {ROLE_WEBHOOK}")
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{ROLE_WEBHOOK}' "
        "AND (rolsuper OR rolbypassrls)) THEN BEGIN "
        f"EXECUTE 'ALTER ROLE {ROLE_WEBHOOK} NOSUPERUSER NOBYPASSRLS'; "
        "EXCEPTION WHEN insufficient_privilege THEN "
        f"RAISE WARNING 'rg0credsplit: cannot normalise {ROLE_WEBHOOK} (%)', SQLERRM; "
        "END; END IF; END $$;"
    )
    # It needs to NAME the functions it may execute; USAGE on the schema is the
    # minimum for that and grants no access to any table on its own.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE_WEBHOOK}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE_WEBHOOK}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE_WEBHOOK}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE ALL ON TABLES FROM {ROLE_WEBHOOK}"
    )

    # ── 2. The restricted application LOGIN ───────────────────────────────
    # No password: a LOGIN role with no password cannot authenticate under
    # scram/md5, so provisioning it here is inert until an operator sets one out
    # of band. On a `trust`-configured local cluster it is immediately usable,
    # which is what lets the test suite run as the real restricted identity.
    op.execute(
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE_API_LOGIN}') THEN "
        f"CREATE ROLE {ROLE_API_LOGIN} LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS "
        "NOCREATEDB NOCREATEROLE; "
        "END IF; END $$;"
    )
    # NOINHERIT is re-asserted on every deploy, not just at creation. It is the
    # single property that keeps a raw connection powerless, and a well-meaning
    # `ALTER ROLE … INHERIT` run by hand would silently hand every direct
    # connection muddato_worker's cross-tenant reach.
    _try(f"ALTER ROLE {ROLE_API_LOGIN} LOGIN NOINHERIT NOCREATEDB NOCREATEROLE",
         f"assert LOGIN/NOINHERIT on {ROLE_API_LOGIN}")
    op.execute(
        "DO $$ BEGIN "
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{ROLE_API_LOGIN}' "
        "AND (rolsuper OR rolbypassrls)) THEN BEGIN "
        f"EXECUTE 'ALTER ROLE {ROLE_API_LOGIN} NOSUPERUSER NOBYPASSRLS'; "
        "EXCEPTION WHEN insufficient_privilege THEN "
        f"RAISE WARNING 'rg0credsplit: cannot clear SUPERUSER/BYPASSRLS on "
        f"{ROLE_API_LOGIN} (%); run it as a superuser before cutting over', SQLERRM; "
        "END; END IF; END $$;"
    )
    for role in AUTHORITY_ROLES:
        _try(f"GRANT {role} TO {ROLE_API_LOGIN}",
             f"grant {role} to {ROLE_API_LOGIN}")

    # USAGE on the schema, and NOTHING else.
    #
    # Deliberate, and worth the sentence: USAGE lets the login RESOLVE a name,
    # it grants no access to anything the name refers to. Without it the server
    # answers "relation does not exist" for a table the login is merely not
    # granted — a refusal that reads like a schema bug, varies with how the
    # schema was created (a `CREATE SCHEMA public` by hand carries no PUBLIC
    # grant; a stock database does), and stops the preflight resolving the very
    # function names it exists to audit. With USAGE, every refusal is the same
    # deterministic "permission denied for table …", and the guarantee rests
    # where it can be reasoned about: no table privileges, and NOINHERIT.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROLE_API_LOGIN}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE_API_LOGIN}")
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE_API_LOGIN}")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE ALL ON TABLES FROM {ROLE_API_LOGIN}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE ALL ON SEQUENCES FROM {ROLE_API_LOGIN}"
    )

    # ── 3. Nobody but the owner may CREATE in public ──────────────────────
    # PG15+ already revokes CREATE from PUBLIC on a fresh database; a cluster
    # restored from an older dump does not. Explicit, and applied to every
    # runtime identity so "can this role create a table" has one answer.
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    for role in (ROLE_API_LOGIN, ROLE_RUNTIME, *AUTHORITY_ROLES):
        op.execute(f"REVOKE CREATE ON SCHEMA public FROM {role}")

    # ``alembic_version`` belongs to the migration identity alone.
    op.execute(f"REVOKE ALL ON alembic_version FROM {ROLE_API_LOGIN}")
    op.execute(f"REVOKE ALL ON alembic_version FROM {ROLE_WEBHOOK}")

    # ── 4. Capability functions ───────────────────────────────────────────
    for signature, grantee, body in CAPABILITIES:
        op.execute(body)
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM {ROLE_RUNTIME}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantee}")

    # ── 5. The pre-authentication identity role loses the directory ───────
    # No replacement policy: with this dropped, `muddato_identity` has NO policy
    # on `users`, and a table with row security on and no matching policy denies.
    # Reads, writes and deletes all fail; the three functions above are the only
    # way in, and each names its row from a credential the caller has proved.
    op.execute("DROP POLICY IF EXISTS rls_users_identity ON users")

    # ── 6. What the removed router-level identity grant was carrying ──────
    # `/auth/me` lists the shops a person belongs to and labels each one's money
    # in that shop's currency. It ran under the identity authority, whose
    # `rls_tenant_settings_identity` policy allowed exactly "the settings of
    # tenants I am a member of". With the router grant gone it runs under
    # `muddato_app` with `app.user_id` set and no tenant scope, and the strict
    # `tenant_id = app_current_tenant()` policy returns nothing — every shop
    # would silently fall back to the UZS default.
    #
    # A SELECT-only second policy carrying the same predicate. Permissive
    # policies OR, so this widens reads by exactly the membership branch and
    # leaves every write on the strict tenant scope. It mirrors
    # `rls_tenants_app_select`, which already has this branch for the same
    # reason: a person may see the shops they belong to before choosing one.
    op.execute("DROP POLICY IF EXISTS rls_tenant_settings_app_self ON tenant_settings")
    op.execute(
        "CREATE POLICY rls_tenant_settings_app_self ON tenant_settings "
        f"FOR SELECT TO {ROLE_APP} USING (EXISTS ("
        "  SELECT 1 FROM public.tenant_users tu "
        "   WHERE tu.tenant_id = tenant_settings.tenant_id "
        "     AND tu.user_id = public.app_current_user()))"
    )

    # ── 7. The verified-customer accessor must see past the new deny ──────
    # `app_verified_telegram_id()` is already SECURITY DEFINER, so it is
    # unaffected by the dropped policy. Re-granted here only so a cluster whose
    # rf0 run warned on the grant is repaired.
    op.execute(
        "REVOKE ALL ON FUNCTION public.app_verified_telegram_id() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.app_verified_telegram_id() "
        f"TO {ROLE_RUNTIME}"
    )


def downgrade() -> None:
    """Restore the pre-correction posture.

    THIS REINTRODUCES TWO AUTHORIZATION DEFECTS. After running it the
    pre-authentication identity role can again read and write every row of
    ``users``, and the capability functions the webhook and identity paths
    depend on are gone. The runtime login is left in place (dropping a role that
    holds memberships wedges a rollback, and an unused login with no password
    grants nothing), but ``muddato_webhook`` loses its only reason to exist.
    """
    op.execute("SET lock_timeout = '5s'")
    op.execute(
        "DO $$ BEGIN RAISE WARNING 'rg0credsplit downgrade: restoring "
        "directory-wide users access for muddato_identity — this REOPENS the "
        "pre-authentication authorization defect closed by this migration'; END $$;"
    )

    op.execute("DROP POLICY IF EXISTS rls_users_identity ON users")
    op.execute(
        f"CREATE POLICY rls_users_identity ON users TO {ROLE_IDENTITY} "
        "USING (true) WITH CHECK (true)"
    )

    for signature, _grantee, _body in CAPABILITIES:
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")

    op.execute("DROP POLICY IF EXISTS rls_tenant_settings_app_self ON tenant_settings")

    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE ALL ON TABLES FROM {ROLE_WEBHOOK}"
    )
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE_WEBHOOK}")
