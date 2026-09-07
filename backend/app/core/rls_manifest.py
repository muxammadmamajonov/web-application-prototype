"""The reviewed row-level-security classification for every public table.

This module is the **manifest**: a human-reviewed statement of what each table
is and which authority may reach it. Three things read it and none of them may
disagree:

* ``alembic/versions/rf0failclosed_fail_closed_rls.py`` — carries its own frozen
  copy (a migration is an immutable snapshot and must never import mutable app
  code), and ``tests/test_rls_manifest_contract.py`` asserts the two agree.
* ``tests/test_rls_matrix.py`` — drives the per-table/per-role security matrix
  from ``CLASSIFICATION`` so a table with no entry cannot be tested, and
  therefore cannot pass.
* ``app.cli.rls_preflight`` — the read-only production posture check.

WHY A MANIFEST AND NOT AUTODISCOVERY. A new tenant table must FAIL until a human
classifies it. Autodiscovery ("anything with a tenant_id gets the tenant policy")
is what produced the r73→r99 drift: ``installment_items`` had no ``tenant_id`` so
it was skipped and shipped policy-less (r94), the global tables were skipped and
shipped deny-all (r95), and ``cron_locks`` was *believed* denied while actually
being fully writable by the restricted role. ``test_rls_matrix`` fails on any
public table absent from ``CLASSIFICATION``, so the manifest cannot silently
auto-approve anything.
"""
from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    """What a table IS, which determines the policy shape it gets."""

    #: Carries ``tenant_id``; a tenant sees exactly its own rows and nothing
    #: else. A ``tenant_id IS NULL`` row is platform-owned and INVISIBLE to a
    #: tenant. Unset/blank/malformed scope yields zero rows and permits no write.
    TENANT_STRICT = "tenant_strict"

    #: ``tenant_strict`` plus a self-membership read branch, so a signed-in user
    #: can discover which tenants they belong to *before* a tenant is resolved.
    #: Without this branch nobody could log in (the login bootstrap reads
    #: memberships with no tenant scope yet).
    TENANT_SELF = "tenant_self"

    #: ``tenant_id`` nullable, where NULL genuinely means "shared catalogue every
    #: tenant reads". Reads see own + global; writes are own-tenant only.
    TENANT_GLOBAL_READ = "tenant_global_read"

    #: No ``tenant_id`` of its own — tenancy is reached through a parent row.
    TENANT_CHILD = "tenant_child"

    #: Cross-tenant identity. Access is self-only or explicitly linked; never
    #: "the whole directory because no tenant was named".
    IDENTITY = "identity"

    #: Platform-owned reference data every tenant reads and only the platform
    #: writes (plan catalogue, brand/colour catalogue, holidays, promos).
    GLOBAL_READ = "global_read"

    #: Key/value platform settings: a small allowlist of keys is tenant-readable,
    #: everything else (and every write) is platform-only.
    PLATFORM_SETTINGS = "platform_settings"

    #: The cross-tenant risk network — reads are open across tenants BY DESIGN
    #: (that is the product), writes are attributable to the writing tenant only.
    RISK_NETWORK = "risk_network"

    #: Never reachable from a tenant, customer or identity request.
    PLATFORM_ONLY = "platform_only"

    #: Scheduler/worker infrastructure. No request-path role may read or write it.
    WORKER_ONLY = "worker_only"

    #: Migration bookkeeping. No runtime role holds ANY privilege on it, which
    #: is the whole protection — RLS is deliberately left off, because enabling
    #: it would mean taking ACCESS EXCLUSIVE on the table Alembic is holding
    #: open to stamp the revision, and a role with no privilege cannot read a
    #: table whether or not row security is on.
    MIGRATION_ONLY = "migration_only"


#: DB roles, by the authority they represent. All are NOLOGIN, NOSUPERUSER,
#: NOBYPASSRLS and own no table; a session assumes one via ``SET LOCAL ROLE``.
ROLE_RUNTIME = "muddato_runtime"      # group role holding the shared DML grants
ROLE_APP = "muddato_app"              # DEFAULT authority: tenant + self
ROLE_CUSTOMER = "muddato_customer"    # a phone-verified customer, across shops
ROLE_IDENTITY = "muddato_identity"    # pre-authentication identity bootstrap
ROLE_WEBHOOK = "muddato_webhook"      # pre-verification provider resolution ONLY
ROLE_WORKER = "muddato_worker"        # scheduler / bot / cron
ROLE_PLATFORM = "muddato_platform"    # platform HQ

#: The ordinary application LOGIN role (``DATABASE_URL``). NOT an authority —
#: an identity that may assume one. LOGIN, NOSUPERUSER, NOBYPASSRLS, NOINHERIT,
#: owns nothing, holds no table privilege, has no CREATE on ``public``.
#: ``app.cli.rls_preflight`` asserts every one of those against the live
#: connection; ``app.config.Settings.db_runtime_role`` can override the name.
ROLE_API_LOGIN = "muddato_api"

#: Every role a request or job may run as. Ordered least → most authority.
#:
#: ``ROLE_WEBHOOK`` is FIRST because it is the narrowest: it is not a member of
#: ``muddato_runtime``, so it holds no privilege on any table at all. It exists
#: for the few statements a public provider callback runs BEFORE it has proved
#: anything — one call to a narrow resolver that returns the provider
#: configuration needed to check the signature, and nothing else.
AUTHORITY_ROLES: tuple[str, ...] = (
    ROLE_WEBHOOK, ROLE_APP, ROLE_CUSTOMER, ROLE_IDENTITY, ROLE_WORKER, ROLE_PLATFORM,
)

#: Roles that hold the shared DML grants through ``muddato_runtime``. Everything
#: else — today only ``muddato_webhook`` — reaches tables through no grant at all.
RUNTIME_MEMBER_ROLES: tuple[str, ...] = (
    ROLE_APP, ROLE_CUSTOMER, ROLE_IDENTITY, ROLE_WORKER, ROLE_PLATFORM,
)

#: The narrow SECURITY DEFINER capability functions, and the ONE role each may
#: be executed by. The preflight asserts owner, ``search_path`` and the exact
#: EXECUTE grant for every entry, so adding a function without classifying it
#: here fails the deploy rather than shipping unreviewed.
#:
#: ``signature`` is the ``pg_get_function_identity_arguments`` form, which is what
#: ``REVOKE``/``GRANT ... ON FUNCTION`` and ``pg_proc`` agree on.
CAPABILITY_FUNCTIONS: dict[str, str] = {
    # pre-verification provider resolution (rg0credsplit)
    "rls_webhook_click_provider(text)": ROLE_WEBHOOK,
    "rls_webhook_payme_provider(text)": ROLE_WEBHOOK,
    "rls_webhook_billing_provider(text)": ROLE_WEBHOOK,
    "rls_webhook_topup_tenant(uuid)": ROLE_WEBHOOK,
    "rls_webhook_topup_tenant_by_ref(text)": ROLE_WEBHOOK,
    # pre-authentication identity bootstrap (rg0credsplit)
    "rls_identity_upsert_telegram(bigint, text, text, text, text, text)": ROLE_IDENTITY,
    "rls_identity_user_state(bigint)": ROLE_IDENTITY,
    "rls_identity_bot_token_candidates(integer)": ROLE_IDENTITY,
    # shared capability resolvers (rf0failclosed) — every authority may use these
    "app_verified_telegram_id()": ROLE_RUNTIME,
    "rls_owning_tenant(text, uuid)": ROLE_RUNTIME,
    "rls_resolve_invitation(text, text[])": ROLE_RUNTIME,
    # the gapless invoice sequence (r99). Pre-dates the manifest and was correct
    # but UNLISTED, which is how the preflight came to be checking only the
    # functions it already knew about — see the note below.
    "next_invoice_sequence(integer)": ROLE_RUNTIME,
}

#: EVERY ``SECURITY DEFINER`` function in ``public`` must appear in
#: ``CAPABILITY_FUNCTIONS``, and the preflight fails on any that does not.
#:
#: This mirrors the rule for tables, and for the same reason. A definer function
#: runs as its OWNER, so it is a privilege-escalation primitive by construction:
#: one added with an unpinned ``search_path`` or ``PUBLIC`` EXECUTE is a hole,
#: and "nobody listed it" must not mean "nobody checks it". An unclassified
#: TABLE has failed the preflight since Batch 5; an unclassified definer
#: FUNCTION did not, until an independent release review noticed that
#: ``next_invoice_sequence`` had been living outside the list — correct, and
#: unchecked.
#:
#: Nothing is exempt. If a future function genuinely belongs here, add it and
#: say who may execute it; that is the review, and it is the point.

#: ``SECURITY DEFINER`` functions that return ``event_trigger`` — reviewed, but
#: NOT capabilities, because nothing can call them.
#:
#: A ``plpgsql`` function returning ``event_trigger`` cannot be invoked by a
#: caller: the compiler refuses it, and an independent review confirmed the
#: refusal across nineteen routes (``SELECT``, ``PERFORM``, ``CALL``, ``FROM``,
#: ``LATERAL``, column ``DEFAULT``, generated column, ``CHECK``, index
#: expression, view, ``RULE``, ``DOMAIN`` constraint, RLS policy expression,
#: dynamic ``EXECUTE``, a SQL-language wrapper, plpgsql assignment, ``OVER``, a
#: row trigger, and a ``regprocedure`` cast). The only caller is the DDL
#: machinery, and only when the event fires. So the three questions
#: ``CAPABILITY_FUNCTIONS`` asks — which role may EXECUTE it, does PUBLIC hold
#: EXECUTE, is the grant narrower than ``muddato_runtime`` — are unanswerable
#: and irrelevant. Classifying one as a capability would fail the deploy on a
#: grant that grants nothing.
#:
#: **The uncallability is a property of the language and the attachment, not of
#: the return type.** The same review demonstrated that ``LANGUAGE internal``
#: and ``LANGUAGE c`` functions declared ``RETURNS event_trigger`` execute
#: normally, and that a function attached to no trigger at all is just a
#: function. Both need superuser, which the deploy identity is not — but an
#: allowlist keyed on a premise that is only usually true is the kind of control
#: that fails quietly, so the preflight asserts the premise instead of assuming
#: it. Every entry here must, live:
#:
#: * return ``pg_catalog.event_trigger`` **by OID** — ``format_type`` renders
#:   ``public.event_trigger`` as the bare word ``event_trigger`` whenever
#:   ``public`` precedes ``pg_catalog`` on the search path, and creating that
#:   type needs only ``CREATE`` on ``public``, which the migration identity has;
#: * back an event trigger that exists and is **not disabled** — this is the
#:   whole justification, so it is checked rather than inferred, and it makes
#:   ``ALTER EVENT TRIGGER … DISABLE`` abort a deploy instead of silently
#:   removing the control;
#: * have a pinned ``search_path`` (asserted even when the function is not
#:   ``SECURITY DEFINER``: a non-definer event-trigger function runs as whoever
#:   issued the DDL, which in production is the schema owner);
#: * not be owned by a role a caller can assume.
#:
#: A name here failing any of those fails the deploy exactly as an unclassified
#: definer would — and is still counted as one, so it fails twice.
#:
#: ``rls_auto_enable`` backs the ``ensure_rls`` DDL event trigger and does one
#: thing: ``ALTER TABLE … ENABLE ROW LEVEL SECURITY`` on any table created in
#: ``public``. It only ever tightens, never grants. It is provisioned OUT OF
#: BAND on the production cluster and is deliberately not created by a
#: migration: ``CREATE EVENT TRIGGER`` needs privileges the deployment identity
#: does not hold, and a migration that cannot succeed everywhere is worse than
#: an operator step that is written down. Absence is therefore not a failure —
#: a cluster without it is simply one where every migration must keep enabling
#: RLS explicitly, which they already do.
EVENT_TRIGGER_FUNCTIONS: frozenset[str] = frozenset({
    "rls_auto_enable()",
})

#: Tables a customer may reach through ``ROLE_CUSTOMER``, with the shape of the
#: link back to the verified person. Anything not listed is invisible to the
#: customer authority — that is the point of enumerating it.
CUSTOMER_VISIBLE: tuple[str, ...] = (
    "users", "customers", "tenants", "tenant_settings",
    "tenant_payment_providers", "installments", "installment_items",
    "payments", "notifications", "receipt_blobs", "media_files",
)

#: Tables the pre-authentication identity bootstrap may reach.
IDENTITY_VISIBLE: tuple[str, ...] = (
    "users", "tenants", "tenant_users", "tenant_settings",
)

#: (child table, parent table, foreign key) for TENANT_CHILD.
CHILD_PARENTS: dict[str, tuple[str, str]] = {
    "installment_items": ("installments", "installment_id"),
    "installment_guarantors": ("installments", "installment_id"),
    "broadcast_recipients": ("broadcasts", "broadcast_id"),
}

#: platform_settings keys a tenant request may SELECT. Everything else is
#: platform-only, and no key is tenant-writable.
PUBLIC_SETTING_KEYS: tuple[str, ...] = (
    "billing_cards", "payment_terms", "referral",
    "business_types", "how_found_options", "demo_plan",
)

#: THE MANIFEST. Every table in schema ``public`` must appear exactly once.
CLASSIFICATION: dict[str, Category] = {
    # ── strict tenant-owned ───────────────────────────────────────────────
    "audit_log": Category.TENANT_STRICT,
    "broadcasts": Category.TENANT_STRICT,
    "consent_records": Category.TENANT_STRICT,
    "customers": Category.TENANT_STRICT,
    "document_deliveries": Category.TENANT_STRICT,
    "guarantors": Category.TENANT_STRICT,
    "installment_item_identifiers": Category.TENANT_STRICT,
    "installments": Category.TENANT_STRICT,
    "invitations": Category.TENANT_STRICT,
    "invoices": Category.TENANT_STRICT,
    "kyc_blobs": Category.TENANT_STRICT,
    "media_files": Category.TENANT_STRICT,
    "notification_channels": Category.TENANT_STRICT,
    "notifications": Category.TENANT_STRICT,
    "payment_review_notices": Category.TENANT_STRICT,
    "payments": Category.TENANT_STRICT,
    "products": Category.TENANT_STRICT,
    "receipt_blobs": Category.TENANT_STRICT,
    "sms_messages": Category.TENANT_STRICT,
    "stock_movements": Category.TENANT_STRICT,
    "tenant_balance_txns": Category.TENANT_STRICT,
    "tenant_payment_providers": Category.TENANT_STRICT,
    "tenant_roles": Category.TENANT_STRICT,
    "tenant_settings": Category.TENANT_STRICT,
    "tenant_staff_invitations": Category.TENANT_STRICT,
    "tenant_subscriptions": Category.TENANT_STRICT,
    "tenant_topups": Category.TENANT_STRICT,
    # ── tenant-owned + self-membership read (login bootstrap) ─────────────
    "tenant_users": Category.TENANT_SELF,
    # ── tenant-owned with a shared global catalogue ───────────────────────
    "catalog_models": Category.TENANT_GLOBAL_READ,
    # ── children, tenancy via the parent row ──────────────────────────────
    "installment_items": Category.TENANT_CHILD,
    "installment_guarantors": Category.TENANT_CHILD,
    "broadcast_recipients": Category.TENANT_CHILD,
    # ── cross-tenant identity ─────────────────────────────────────────────
    "users": Category.IDENTITY,
    "tenants": Category.IDENTITY,
    # ── platform-owned reference data, tenant-readable ────────────────────
    "plans": Category.GLOBAL_READ,
    "catalog_brands": Category.GLOBAL_READ,
    "catalog_categories": Category.GLOBAL_READ,
    "catalog_colors": Category.GLOBAL_READ,
    "holidays": Category.GLOBAL_READ,
    "promo_codes": Category.GLOBAL_READ,
    "ad_campaigns": Category.GLOBAL_READ,
    # ── key/value platform settings ───────────────────────────────────────
    "platform_settings": Category.PLATFORM_SETTINGS,
    # ── the cross-tenant risk network ─────────────────────────────────────
    "customer_risk_signals": Category.RISK_NETWORK,
    # ── platform-only ─────────────────────────────────────────────────────
    "sequence_counters": Category.PLATFORM_ONLY,
    "referrals": Category.PLATFORM_ONLY,
    "webhook_dlq": Category.PLATFORM_ONLY,
    # ── scheduler infrastructure ──────────────────────────────────────────
    "cron_locks": Category.WORKER_ONLY,
    "apscheduler_jobs": Category.WORKER_ONLY,
    # ── migration bookkeeping ─────────────────────────────────────────────
    "alembic_version": Category.MIGRATION_ONLY,
}

#: Categories whose rows a tenant request must never see without a tenant scope.
#: Used by the preflight to decide what "fail-open" means for a given table.
TENANT_SCOPED_CATEGORIES: frozenset[Category] = frozenset({
    Category.TENANT_STRICT,
    Category.TENANT_SELF,
    Category.TENANT_GLOBAL_READ,
    Category.TENANT_CHILD,
})


#: FORCE ROW LEVEL SECURITY, decided per category rather than as one blanket
#: rule — the brief asks for the decision to be explicit, and "we didn't" is not
#: a decision.
#:
#: The answer is False everywhere, for one reason that applies to every category:
#: FORCE subjects the TABLE OWNER to row security, and the owner is the identity
#: that runs migrations. r61, r69, ra0, rb0 and rc0 all rewrite rows; under FORCE
#: each of those would have had to carry a policy for the migration role, which
#: is a policy whose entire purpose is to be permissive — the bypass moved, not
#: removed, and now spelled out in SQL where it is easy to forget to delete.
#:
#: What FORCE would have bought — "the runtime cannot bypass via ownership" — is
#: instead guaranteed where it is testable: the runtime downgrades to an
#: authority role at EVERY transaction, no authority role owns a table, and
#: `app.cli.rls_preflight` exits non-zero if either stops being true.
FORCE_RLS_BY_CATEGORY: dict[Category, bool] = dict.fromkeys(Category, False)


#: Which authority is EXPECTED to reach each category, and how. This is the
#: "expected tenant / customer / platform / worker path" column of the Batch 5
#: inventory, written down once so the generated report and the reader agree.
EXPECTED_PATHS: dict[Category, dict[str, str]] = {
    Category.TENANT_STRICT: {
        "tenant": "own tenant_id only", "customer": "no (unless listed below)",
        "platform": "all", "worker": "all",
    },
    Category.TENANT_SELF: {
        "tenant": "own tenant_id + own memberships", "customer": "no",
        "platform": "all", "worker": "all",
    },
    Category.TENANT_GLOBAL_READ: {
        "tenant": "own + shared catalogue (read); own only (write)",
        "customer": "shared catalogue (read)", "platform": "all", "worker": "all",
    },
    Category.TENANT_CHILD: {
        "tenant": "via parent row's tenant_id", "customer": "via own contract",
        "platform": "all", "worker": "all",
    },
    Category.IDENTITY: {
        "tenant": "self + rows linked to the scoped tenant", "customer": "self",
        "platform": "all", "worker": "all",
    },
    Category.GLOBAL_READ: {
        "tenant": "read", "customer": "read", "platform": "all", "worker": "all",
    },
    Category.PLATFORM_SETTINGS: {
        "tenant": "allowlisted keys (read)", "customer": "allowlisted keys (read)",
        "platform": "all", "worker": "all",
    },
    Category.RISK_NETWORK: {
        "tenant": "read all (by design); write own name",
        "customer": "no", "platform": "all", "worker": "all",
    },
    Category.PLATFORM_ONLY: {
        "tenant": "no", "customer": "no", "platform": "all", "worker": "all",
    },
    Category.WORKER_ONLY: {
        "tenant": "no", "customer": "no", "platform": "all", "worker": "all",
    },
    Category.MIGRATION_ONLY: {
        "tenant": "no", "customer": "no", "platform": "no", "worker": "no",
    },
}


def tables_in(*categories: Category) -> tuple[str, ...]:
    """Manifest tables in the given categories, sorted for stable iteration."""
    wanted = set(categories)
    return tuple(sorted(t for t, c in CLASSIFICATION.items() if c in wanted))


__all__ = (
    "AUTHORITY_ROLES",
    "CAPABILITY_FUNCTIONS",
    "CHILD_PARENTS",
    "CLASSIFICATION",
    "CUSTOMER_VISIBLE",
    "EVENT_TRIGGER_FUNCTIONS",
    "EXPECTED_PATHS",
    "FORCE_RLS_BY_CATEGORY",
    "IDENTITY_VISIBLE",
    "PUBLIC_SETTING_KEYS",
    "ROLE_API_LOGIN",
    "ROLE_APP",
    "ROLE_CUSTOMER",
    "ROLE_IDENTITY",
    "ROLE_PLATFORM",
    "ROLE_RUNTIME",
    "ROLE_WEBHOOK",
    "ROLE_WORKER",
    "RUNTIME_MEMBER_ROLES",
    "TENANT_SCOPED_CATEGORIES",
    "Category",
    "tables_in",
)
