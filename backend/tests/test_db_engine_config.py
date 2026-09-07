"""#68 — DB engine connection budget + query/lock timeouts are env-driven."""
from __future__ import annotations

import pytest

from app import db
from app.config import settings

DIRECT_URL = "postgresql+asyncpg://u:p@db.example.com:5432/postgres"
POOLER_URL = "postgresql+asyncpg://u:p@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"


def test_pool_size_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_pool_size", 7)
    monkeypatch.setattr(settings, "db_max_overflow", 11)
    kw = db._engine_kwargs(DIRECT_URL)
    assert kw["pool_size"] == 7
    assert kw["max_overflow"] == 11
    assert kw["pool_pre_ping"] is True
    assert kw["pool_recycle"] == 1800


def test_command_timeout_from_statement_timeout_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 25_000)
    kw = db._engine_kwargs(DIRECT_URL)
    # asyncpg command_timeout is in seconds and is set on EVERY connection
    # (pooler-safe) so a stuck query can't pile up.
    assert kw["connect_args"]["command_timeout"] == 25.0


def test_direct_connection_enforces_server_side_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "db_statement_timeout_ms", 30_000)
    monkeypatch.setattr(settings, "db_lock_timeout_ms", 10_000)
    ss = db._engine_kwargs(DIRECT_URL)["connect_args"]["server_settings"]
    assert ss["statement_timeout"] == "30000"
    assert ss["lock_timeout"] == "10000"


def test_transaction_pooler_disables_prepared_stmts_and_skips_server_settings() -> None:
    ca = db._engine_kwargs(POOLER_URL)["connect_args"]
    assert ca["statement_cache_size"] == 0
    assert ca["prepared_statement_cache_size"] == 0
    # The transaction pooler forbids session GUCs → no server_settings; the
    # client-side command_timeout still applies.
    assert "server_settings" not in ca
    assert "command_timeout" in ca


def test_pooler_detection() -> None:
    assert db._is_transaction_pooler(POOLER_URL) is True
    assert db._is_transaction_pooler(DIRECT_URL) is False
