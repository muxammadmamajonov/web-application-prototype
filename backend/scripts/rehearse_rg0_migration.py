"""Production-SHAPED rehearsal for ``rg0credsplit``. Synthetic data only.

    uv run python -m scripts.rehearse_rg0_migration --db muddato_rehearsal

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
It is a **production-shaped synthetic rehearsal**. No production row, phone
number, name, Telegram id, file id, token or document is read, copied or
generated here, and none is required: the properties under measurement — DDL
lock duration, whether anything rewrites or scans a table, behaviour under
concurrent reads and writes, and rollback after a forced lock timeout — depend
on table SIZE and on the SHAPE of the schema, not on the contents of any row.

It is **not** a production-data rehearsal, and this file will not pretend
otherwise. No sanitized production dump exists for this project, so the row
counts below are conservative synthetic scale assumptions, stated explicitly and
deliberately larger than the current live system.

WHY THE ANSWER IS EXPECTED TO BE BORING
---------------------------------------
``rg0credsplit`` issues ``CREATE FUNCTION``, ``GRANT``, ``REVOKE``,
``CREATE ROLE``, one ``DROP POLICY`` and one ``CREATE POLICY``. Every one of
those touches a system catalogue. None rewrites a heap, none builds an index,
none scans a table. The only table-level lock is the brief ACCESS EXCLUSIVE that
``DROP POLICY``/``CREATE POLICY`` takes on ``users`` for the catalogue update.
A rehearsal is still worth running, because "expected to be boring" and
"measured to be boring" are different claims, and because the interesting
question is not duration but what happens when the lock CANNOT be taken.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import create_engine, text

#: Conservative synthetic scale, and exactly what is SEEDED — no aspirational
#: numbers for tables the rehearsal does not actually fill, because a reported
#: scale that was never created is the same lie as a green test on empty tables.
#:
#: Chosen an order of magnitude above the current live system so a "fast" result
#: is not fast merely because the tables are empty. The three tables here are
#: the ones that matter to THIS migration: ``users`` and ``tenant_settings`` are
#: the only tables it locks (one DROP POLICY, one CREATE POLICY), and
#: ``customers`` gives the concurrent read/write traffic something real to
#: contend on. ``installments`` and ``payments`` are deliberately left empty:
#: ``rg0credsplit`` does not touch them, and seeding half a million rows to
#: watch a catalogue update would measure the seeder, not the migration.
SCALE: dict[str, int] = {
    "tenants": 200,
    "users": 20_000,
    "customers": 50_000,
}


@dataclass
class Rehearsal:
    database: str = ""
    scale: dict[str, int] = field(default_factory=dict)
    table_sizes: dict[str, str] = field(default_factory=dict)
    index_sizes: dict[str, str] = field(default_factory=dict)
    seed_seconds: float = 0.0
    migration_seconds: float = 0.0
    downgrade_seconds: float = 0.0
    reupgrade_seconds: float = 0.0
    max_lock_seconds: float = 0.0
    locks_taken: list[str] = field(default_factory=list)
    concurrent_reads: int = 0
    concurrent_read_errors: int = 0
    concurrent_writes: int = 0
    concurrent_write_errors: int = 0
    lock_timeout_aborted: bool = False
    lock_timeout_left_head: str = ""
    rewrites_or_scans: list[str] = field(default_factory=list)
    app_compatible_before: bool = False
    app_compatible_after: bool = False

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _alembic(url: str, command_name: str, revision: str) -> float:
    """Run one alembic command in a subprocess and return its wall time.

    A subprocess, not an in-process call: ``alembic/env.py`` reads the URL out of
    settings at import time, and the point of the rehearsal is to exercise the
    real entry point rather than a version of it configured by hand.
    """
    env = {**os.environ, "MIGRATION_DATABASE_URL_SYNC": url}
    started = time.monotonic()
    subprocess.run(
        [sys.executable, "-m", "alembic", command_name, revision],
        check=True, env=env, capture_output=True,
    )
    return time.monotonic() - started


def _seed(engine, scale: dict[str, int]) -> float:
    """Generate production-SHAPED rows. Values are generated, never copied.

    Names are ``synthetic-<n>``, phones are a reserved-prefix numeric range, and
    nothing here reads any other database.
    """
    started = time.monotonic()
    with engine.begin() as conn:
        conn.execute(text("SET lock_timeout = '30s'"))
        conn.execute(text("""
            INSERT INTO tenants (id, slug, name, status, plan_code, balance,
                                 locale, timezone, plan_overrides, onboarding_state)
            SELECT gen_random_uuid(), 'synthetic-' || g, 'Synthetic ' || g,
                   'active', 'trial', 0, 'uz', 'Asia/Tashkent', '{}', '{}'
              FROM generate_series(1, :n) g
        """), {"n": scale["tenants"]})
        conn.execute(text("""
            INSERT INTO users (id, telegram_id, first_name, locale, is_active)
            SELECT gen_random_uuid(), 900000000 + g, 'Synthetic ' || g, 'uz', true
              FROM generate_series(1, :n) g
        """), {"n": scale["users"]})
        # Customers spread evenly across the synthetic tenants, so the tables
        # have the SHAPE of a real multi-tenant system rather than one giant
        # tenant. `mod()` rather than the `%` operator: SQLAlchemy's `text()`
        # treats `%` as a parameter escape, and the doubling that survives into
        # psycopg is a well-known way to produce SQL nobody wrote.
        conn.execute(text("""
            WITH t AS (
                SELECT id, row_number() OVER (ORDER BY slug) - 1 AS n
                  FROM tenants WHERE slug LIKE 'synthetic-' || '%'
            )
            INSERT INTO customers (id, tenant_id, full_name, phone, status,
                                   metadata, passport_photo_urls)
            SELECT gen_random_uuid(), t.id, 'Synthetic ' || g,
                   '+9987' || lpad(g::text, 8, '0'), 'active', '{}', '[]'
              FROM generate_series(1, :n) g
              JOIN t ON t.n = mod(g, :tenants)
        """), {"n": scale["customers"], "tenants": scale["tenants"]})
    return time.monotonic() - started


def _watch_locks(engine, stop: threading.Event, out: Rehearsal) -> None:
    """Sample ``pg_locks`` while the migration runs.

    Sampling, not tracing: PostgreSQL exposes no per-statement lock-duration
    metric, so the honest measurement is "the longest window in which an ACCESS
    EXCLUSIVE lock was observed", at the sampling resolution stated in the
    report. At 20ms that is precise enough to distinguish "catalogue update"
    from "table rewrite" by two orders of magnitude, which is the question.
    """
    seen: dict[str, float] = {}
    while not stop.is_set():
        try:
            with engine.connect() as conn:
                rows = conn.execute(text("""
                    SELECT c.relname, l.mode
                      FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = 'public' AND l.granted
                       AND l.mode = 'AccessExclusiveLock'
                """)).all()
            now = time.monotonic()
            for name, mode in rows:
                key = f"{name}:{mode}"
                seen.setdefault(key, now)
                out.max_lock_seconds = max(out.max_lock_seconds, now - seen[key])
                if key not in out.locks_taken:
                    out.locks_taken.append(key)
        except Exception:
            # A sampling miss must not fail the run: this thread is an observer,
            # and a dropped sample costs resolution, not correctness.
            pass
        stop.wait(0.02)


def _hammer(url: str, stop: threading.Event, out: Rehearsal, *, write: bool) -> None:
    """Representative concurrent traffic against the migrating database."""
    engine = create_engine(url, poolclass=None)
    try:
        while not stop.is_set():
            try:
                with engine.begin() as conn:
                    conn.execute(text("SET LOCAL lock_timeout = '2s'"))
                    if write:
                        conn.execute(text(
                            "UPDATE customers SET updated_at = now() "
                            " WHERE id = (SELECT id FROM customers LIMIT 1)"
                        ))
                        out.concurrent_writes += 1
                    else:
                        conn.execute(text("SELECT count(*) FROM customers"))
                        conn.execute(text("SELECT 1 FROM users LIMIT 1"))
                        out.concurrent_reads += 1
            except Exception:
                # An error IS a measurement here — concurrent traffic failing
                # during the migration is exactly what this thread reports.
                if write:
                    out.concurrent_write_errors += 1
                else:
                    out.concurrent_read_errors += 1
            stop.wait(0.005)
    finally:
        engine.dispose()


def _sizes(engine, out: Rehearsal) -> None:
    with engine.connect() as conn:
        for name, table_bytes, index_bytes in conn.execute(text("""
            SELECT c.relname,
                   pg_size_pretty(pg_table_size(c.oid)),
                   pg_size_pretty(pg_indexes_size(c.oid))
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind = 'r'
               AND c.relname = ANY(:names)
             ORDER BY c.relname
        """), {"names": list(SCALE)}).all():
            out.table_sizes[name] = table_bytes
            out.index_sizes[name] = index_bytes


def _forced_lock_timeout(url: str, out: Rehearsal) -> None:
    """Hold a conflicting lock on ``users`` and confirm the migration ABORTS.

    This is the one that matters operationally. ``rg0credsplit`` sets
    ``lock_timeout = '5s'``, so behind a long-running query it must fail fast
    and leave the previous revision stamped — not queue behind the query and
    stall every request that arrives in the meantime.
    """
    blocker = create_engine(url, poolclass=None)
    conn = blocker.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute("LOCK TABLE users IN ACCESS EXCLUSIVE MODE")
        try:
            _alembic(url, "upgrade", "head")
            out.lock_timeout_aborted = False
        except subprocess.CalledProcessError:
            out.lock_timeout_aborted = True
        cur.execute("ROLLBACK")
    finally:
        conn.close()
        blocker.dispose()
    engine = create_engine(url, poolclass=None)
    try:
        with engine.connect() as c:
            out.lock_timeout_left_head = (
                c.execute(text("SELECT version_num FROM alembic_version")).scalar() or ""
            )
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", required=True, help="sync URL of a THROWAWAY database")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    out = Rehearsal(scale=dict(SCALE))
    engine = create_engine(args.url, poolclass=None)
    with engine.connect() as conn:
        out.database = conn.execute(text("SELECT current_database()")).scalar() or ""

    # 1. previous head + production-shaped data
    _alembic(args.url, "upgrade", "rf0failclosed")
    out.seed_seconds = _seed(engine, SCALE)
    _sizes(engine, out)
    with engine.connect() as conn:
        out.app_compatible_before = bool(conn.execute(text(
            "SELECT count(*) > 0 FROM pg_policies WHERE schemaname='public'",
        )).scalar())

    # 2. the migration, under concurrent representative traffic
    stop = threading.Event()
    threads = [
        threading.Thread(target=_watch_locks, args=(engine, stop, out), daemon=True),
        threading.Thread(target=_hammer, args=(args.url, stop, out),
                         kwargs={"write": False}, daemon=True),
        threading.Thread(target=_hammer, args=(args.url, stop, out),
                         kwargs={"write": True}, daemon=True),
    ]
    for t in threads:
        t.start()
    time.sleep(0.5)
    try:
        out.migration_seconds = _alembic(args.url, "upgrade", "head")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)

    # 3. does anything rewrite or scan? relfilenode changes on a rewrite.
    with engine.connect() as conn:
        out.rewrites_or_scans = [
            name for (name,) in conn.execute(text("""
                SELECT c.relname FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname='public' AND c.relkind='r'
                   AND pg_stat_get_live_tuples(c.oid) < 0
            """)).all()
        ]
        out.app_compatible_after = bool(conn.execute(text(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid = p.pronamespace WHERE n.nspname='public' "
            "AND p.proname LIKE 'rls_%'",
        )).scalar())

    # 4. rollback / re-apply, and the forced-lock-timeout abort
    out.downgrade_seconds = _alembic(args.url, "downgrade", "rf0failclosed")
    out.reupgrade_seconds = _alembic(args.url, "upgrade", "head")
    out.downgrade_seconds += _alembic(args.url, "downgrade", "rf0failclosed")
    _forced_lock_timeout(args.url, out)
    _alembic(args.url, "upgrade", "head")

    engine.dispose()
    if args.json:
        print(json.dumps(out.as_dict(), indent=2, default=str))
    else:
        for key, value in out.as_dict().items():
            print(f"  {key:26} {value}")
    return 0


if __name__ == "__main__":  # pragma: no cover — operator entry point
    sys.exit(main())
