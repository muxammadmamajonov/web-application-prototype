"""The manifest, the migration and the live schema must all say the same thing.

Three artefacts describe the RLS classification and each exists for a different
reason: the migration carries a frozen copy (an immutable snapshot must not
import mutable app code), ``app.core.rls_manifest`` is the version tests and the
preflight read, and the database is the only one that is actually load-bearing.
Two of the three agreeing is how a fix gets applied everywhere except where it
matters, so this pins all three together.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.core import rls_manifest as manifest

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "rf0failclosed_fail_closed_rls.py"
)


def _migration_module(stem: str = "rf0failclosed_fail_closed_rls"):
    """Load one migration's frozen snapshot by file stem.

    A migration never imports ``app.core.rls_manifest`` — it is an immutable
    record of what was true when it ran — so each carries its own copy of the
    names it needs, and this module is what stops the copies drifting.
    """
    path = MIGRATION.with_name(f"{stem}.py")
    spec = importlib.util.spec_from_file_location(stem, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_the_migrations_frozen_copy_matches_the_manifest() -> None:
    """Same tables, same categories, in both places.

    ``TENANT_STRICT`` is the union of rf0's frozen tuple and every later
    migration's own frozen contribution — currently just ``rk0catident``,
    which created ``installment_item_identifiers``, the first new table since
    rf0. Same reasoning as ``test_the_role_names_match_the_migration`` below:
    asserting equality against rf0 alone would fail forever, and a migration
    is never rewritten after it ships.
    """
    m = _migration_module()
    later_table = _migration_module("rk0catident_installment_item_identifiers")
    C = manifest.Category

    expected = {
        C.TENANT_STRICT: set(m.TENANT_STRICT) | set(later_table.TENANT_STRICT),
        C.TENANT_SELF: set(m.TENANT_SELF),
        C.TENANT_GLOBAL_READ: set(m.TENANT_GLOBAL_READ),
        C.TENANT_CHILD: {child for child, _p, _f in m.TENANT_CHILD},
        C.IDENTITY: set(m.IDENTITY_TABLES),
        C.GLOBAL_READ: set(m.GLOBAL_READ),
        C.PLATFORM_ONLY: set(m.PLATFORM_ONLY),
        C.WORKER_ONLY: set(m.WORKER_ONLY),
        C.MIGRATION_ONLY: set(m.MIGRATION_ONLY),
        C.PLATFORM_SETTINGS: {"platform_settings"},
        C.RISK_NETWORK: {"customer_risk_signals"},
    }
    for category, tables in expected.items():
        got = set(manifest.tables_in(category))
        assert got == tables, (
            f"{category}: manifest has {sorted(got)}, migration has {sorted(tables)}"
        )


def test_the_child_parent_map_matches_the_migration() -> None:
    m = _migration_module()
    assert {
        child: (parent, fk) for child, parent, fk in m.TENANT_CHILD
    } == manifest.CHILD_PARENTS


def test_the_public_settings_allowlist_matches_the_migration() -> None:
    m = _migration_module()
    assert set(manifest.PUBLIC_SETTING_KEYS) == set(m.PUBLIC_SETTING_KEYS)


def test_the_role_names_match_the_migration() -> None:
    """The frozen role list spans TWO migrations, and both must agree.

    ``rf0failclosed`` created five authority roles; ``rg0credsplit`` added
    ``muddato_webhook`` (and the ``muddato_api`` login, which is an identity
    rather than an authority). Each migration carries its own frozen snapshot,
    so the manifest is checked against their UNION — asserting equality with
    rf0 alone would either fail forever or force rewriting a shipped migration.
    """
    m = _migration_module()
    later = _migration_module("rg0credsplit_runtime_credential_split")
    assert set(manifest.AUTHORITY_ROLES) == set(m.AUTHORITY_ROLES) | set(
        later.AUTHORITY_ROLES,
    )
    assert set(manifest.AUTHORITY_ROLES) == set(later.AUTHORITY_ROLES)
    assert manifest.ROLE_RUNTIME == m.ROLE_RUNTIME == later.ROLE_RUNTIME
    assert manifest.ROLE_WEBHOOK == later.ROLE_WEBHOOK
    assert manifest.ROLE_API_LOGIN == later.ROLE_API_LOGIN
    # The login is NOT an authority: it may assume one, and holds none itself.
    assert manifest.ROLE_API_LOGIN not in manifest.AUTHORITY_ROLES


def test_every_manifest_table_has_exactly_one_category() -> None:
    """A dict cannot hold a duplicate key, so the real assertion is the count:
    the categories partition the manifest with nothing left over."""
    total = sum(
        len(manifest.tables_in(c)) for c in manifest.Category
    )
    assert total == len(manifest.CLASSIFICATION)


def test_the_customer_visible_set_is_a_subset_of_the_manifest() -> None:
    unknown = set(manifest.CUSTOMER_VISIBLE) - set(manifest.CLASSIFICATION)
    assert not unknown, f"CUSTOMER_VISIBLE names unknown table(s): {sorted(unknown)}"
    unknown = set(manifest.IDENTITY_VISIBLE) - set(manifest.CLASSIFICATION)
    assert not unknown, f"IDENTITY_VISIBLE names unknown table(s): {sorted(unknown)}"


def test_the_migration_reaches_the_declared_revision() -> None:
    m = _migration_module()
    assert m.revision == "rf0failclosed"
    assert m.down_revision == "re2noticeretry"


def test_the_two_definer_buckets_are_disjoint() -> None:
    """A function is a capability or an event-trigger definer, never both.

    They are checked by different rules: a capability must name exactly one role
    that may EXECUTE it, an event-trigger definer must be callable by nobody. A
    signature in both would be judged twice under contradictory premises, and
    ``_collect_capabilities`` would report it as accepted while also asserting a
    grant that cannot exist.
    """
    overlap = set(manifest.EVENT_TRIGGER_FUNCTIONS) & set(manifest.CAPABILITY_FUNCTIONS)
    assert not overlap, (
        "signature(s) classified as BOTH a capability and an event-trigger "
        f"definer: {sorted(overlap)}"
    )


def test_event_trigger_functions_are_argumentless_signatures() -> None:
    """The shape ``pg_proc`` will be matched against, pinned.

    ``_collect_capabilities`` keys the live inventory on
    ``proname(oidvectortypes(proargtypes))``. An event trigger's function takes
    no arguments — Postgres requires it — so anything with a parameter list here
    could never match a live row, and would silently report as "absent from this
    cluster" instead of failing. Cheap to assert, invisible to debug.
    """
    for signature in manifest.EVENT_TRIGGER_FUNCTIONS:
        assert signature.endswith("()"), (
            f"{signature!r} declares arguments; an event-trigger function takes none, "
            "so this entry can never match a live function"
        )
