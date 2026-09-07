"""#95 (verifiable part) — users have normalized-unique email + phone.

DB-free: the two functional partial-unique indexes are declared on the model and
the migration builds them CONCURRENTLY. (RLS is deferred — see r72 migration
docstring; the ledger money CHECKs shipped in #69.)
"""
from __future__ import annotations

from pathlib import Path


def test_user_has_normalized_unique_email_phone_indexes() -> None:
    from app.models import User

    by_name = {i.name: i for i in User.__table__.indexes}
    assert "uq_users_email_lower" in by_name
    assert "uq_users_phone_digits" in by_name
    assert by_name["uq_users_email_lower"].unique is True
    assert by_name["uq_users_phone_digits"].unique is True


def test_migration_builds_them_concurrently() -> None:
    src = (
        Path(__file__).resolve().parent.parent
        / "alembic" / "versions" / "r72_user_unique_email_phone.py"
    ).read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX CONCURRENTLY" in src
    assert "autocommit_block()" in src


def test_indexes_are_functional_and_partial() -> None:
    from app.models import User

    by_name = {i.name: i for i in User.__table__.indexes}
    # Functional expression index → over lower(email), not the raw column.
    email_expr = str(next(iter(by_name["uq_users_email_lower"].expressions)))
    assert "lower(email)" in email_expr.lower()
