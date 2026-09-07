"""One phone = one person, and the rule survives formatting.

THE BUG THIS PINS (production, 2026-08-06). A number already registered could
be registered again. `uq_users_phone_digits` existed, was UNIQUE, showed up
perfectly in `pg_indexes` — and was marked `indisvalid = false`, because a
`CREATE INDEX CONCURRENTLY` had aborted on pre-existing duplicates and left the
carcass behind. Postgres silently ignores such an index. Two duplicate pairs
were live, one created the day before.

An invalid index cannot be caught from the model layer, so what IS checked here
is everything that can be: that one canonical form exists, that every model
agrees on what makes two records the same person, and that a violation reaches
the user as an actionable message rather than a bare `conflict`.

The database behaviour itself was verified by hand against a migrated database
after r96phoneuniq, all three cases:
  * '+998 90 111 22 33' when '+998901112233' exists  → REFUSED
  * '901112233'         when '+998901112233' exists  → REFUSED
  * '+998 93 000 11 22' (a different number)         → accepted
The second case is the one worth keeping: before the index expression applied
the country code, a bare local number inserted cleanly next to its own 998…
form. Plain digits are NOT an identity for a phone number.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from app.core.validators import _uz_phone
from app.models.base import PHONE_IDENTITY_SQL
from app.services.sms_eskiz import canonical_uz_phone

MIGRATION = (pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
             / "r96phoneuniq_canonical_phone_uniqueness.py")


# ── one canonical stored form ────────────────────────────────────────────────

@pytest.mark.parametrize("written", [
    "+998901112233", "998901112233", "901112233",
    "+998 90 111 22 33", "998-90-111-22-33", "  +998 90 111 22 33  ",
])
def test_every_way_of_writing_it_stores_the_same_string(written):
    assert canonical_uz_phone(written) == "+998901112233"


def test_the_validator_canonicalises_rather_than_echoing_the_input():
    # This is the behaviour change: it used to return `v` untouched, which is
    # what let one shop hold the same customer twice.
    assert _uz_phone("+998 90 111 22 33") == "+998901112233"
    assert _uz_phone("901112233") == "+998901112233"


def test_blank_still_passes_through_for_optional_fields():
    for blank in (None, "", "   "):
        assert _uz_phone(blank) == blank


@pytest.mark.parametrize("junk", ["+7 900 123 45 67", "12345", "not a phone", "+998 12 3"])
def test_a_number_that_is_not_a_uz_mobile_is_refused(junk):
    assert canonical_uz_phone(junk) is None
    with pytest.raises(ValueError, match="valid Uzbek number"):
        _uz_phone(junk)


def test_canonicalising_is_idempotent():
    once = canonical_uz_phone("+998 90 111 22 33")
    assert canonical_uz_phone(once) == once


# ── every model agrees on what "the same person" means ───────────────────────

def test_all_three_phone_indexes_share_one_expression():
    """users, customers and guarantors must not drift apart.

    If `users` treated a local number as distinct while `customers` did not,
    the same person would be one record in a shop and two on the platform.
    """
    import app.models.customer as customer
    import app.models.guarantor as guarantor
    import app.models.user as user

    for mod in (user, customer, guarantor):
        assert mod.PHONE_IDENTITY_SQL is PHONE_IDENTITY_SQL, (
            f"{mod.__name__} imported its own copy of the phone identity rule"
        )


def test_the_identity_expression_applies_the_country_code():
    """The property that a plain digits index did NOT have.

    Verified against the database: without this branch, '901112233' inserts
    happily alongside '+998901112233'.
    """
    assert "length(regexp_replace(phone, '[^0-9]', '', 'g')) = 9" in PHONE_IDENTITY_SQL
    assert "'998' ||" in PHONE_IDENTITY_SQL


def test_the_migration_mirrors_the_model_expression():
    """The migration builds the live index; the model builds the test one. If
    they disagree, tests pass against a shape production does not have — which
    is exactly how the child-table RLS outage stayed invisible."""
    spec = importlib.util.spec_from_file_location("r96phoneuniq", MIGRATION)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def normalise(sql: str) -> str:
        """Collapse whitespace and drop one layer of wrapping parens.

        The two differ by exactly that wrapper and must: Postgres rejects
        `CREATE INDEX … ON t (CASE … END)` and needs `((CASE … END))`. The model
        constant carries its own pair because SQLAlchemy adds only one around a
        `text()` key, while the migration writes both itself. Comparing the
        LOGIC is the point — a real drift in the rule still fails here.
        """
        s = " ".join(sql.split())
        while s.startswith("(") and s.endswith(")"):
            s = s[1:-1].strip()
        return s

    assert normalise(mod._DIGITS.format(col="phone")) == normalise(PHONE_IDENTITY_SQL)


def test_the_model_expression_is_parenthesised_for_postgres():
    """Guards the regression that broke every DB-backed test.

    `create_all` emitted `ON users (CASE … END)`, which is a syntax error, so
    the entire integration tier failed at fixture setup — invisibly, because it
    self-skips without TEST_DATABASE_URL.
    """
    assert PHONE_IDENTITY_SQL.startswith("(") and PHONE_IDENTITY_SQL.endswith(")"), (
        "the index expression must carry its own parentheses or "
        "Base.metadata.create_all fails outright"
    )


def test_the_migration_refuses_to_build_an_index_that_would_fail():
    """The dead index was born from a build that aborted on duplicates. This
    one checks first and raises with the numbers named."""
    src = MIGRATION.read_text(encoding="utf8")
    assert "RAISE EXCEPTION" in src
    assert "DROP INDEX IF EXISTS uq_users_phone_digits" in src, (
        "an INVALID index still occupies the name — CREATE alone would collide"
    )


# ── a violation says what is wrong ───────────────────────────────────────────

def test_the_platform_wide_constraints_map_to_actionable_codes():
    """Without these, a taken number arrived as a bare `conflict` and the client
    rendered "this data has changed, refresh the page" — useless advice for a
    problem the person can actually fix."""
    from app.main import _INTEGRITY_CODES

    assert _INTEGRITY_CODES["uq_users_phone_digits"] == "phone_already_registered"
    assert _INTEGRITY_CODES["uq_users_email_lower"] == "email_already_registered"
    # The per-shop ones must keep working — r96 renames nothing.
    assert _INTEGRITY_CODES["uq_customer_tenant_phone"] == "customer_phone_exists"
    assert _INTEGRITY_CODES["uq_guarantor_tenant_phone"] == "guarantor_phone_exists"
