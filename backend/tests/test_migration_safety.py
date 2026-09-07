"""#76 — deploy-safe migrations.

Migrations need a live DB to actually run (CI applies the chain online against a
postgres service), so these are source-level guards that lock in the
deploy-safety properties: a future edit can't silently reintroduce a blocking
index build or a destructive unconditional DELETE.
"""
from __future__ import annotations

from pathlib import Path

_ALEMBIC = Path(__file__).resolve().parent.parent / "alembic"
_VERSIONS = _ALEMBIC / "versions"


def test_payments_unique_index_builds_concurrently() -> None:
    src = (_VERSIONS / "r57_payment_external_ref_unique.py").read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX CONCURRENTLY" in src
    assert "autocommit_block()" in src
    # The blocking helper (ACCESS EXCLUSIVE lock) must not come back.
    assert "op.create_index(" not in src


def test_catalog_clear_migration_is_neutralized() -> None:
    src = (_VERSIONS / "r41_clear_catalog.py").read_text(encoding="utf-8")
    # The unconditional destructive DELETE must be gone.
    assert "DELETE FROM catalog" not in src


def test_env_runs_one_transaction_per_migration() -> None:
    env = (_ALEMBIC / "env.py").read_text(encoding="utf-8")
    # Per-migration transactions: a late failure can't roll back the whole chain,
    # and a migration can drop into an autocommit_block for CONCURRENTLY DDL.
    assert "transaction_per_migration=True" in env
