"""MUDD-95 — the migration advisory lock actually serializes.

``conftest.py``'s ``migrated_db`` fixture wraps ``alembic upgrade head`` in
``SELECT pg_advisory_lock(1)`` on the migration (schema-owner) connection to
stop two concurrent upgrades from deadlocking on the ``AccessExclusiveLock``
that ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` takes per table (CI run
33097890279). That fixture cannot itself prove serialization: pytest-xdist
was reverted (0b298cd9), so there is no longer a way to run two real
``migrated_db`` instances against the same database at once in this suite.

This test proves the underlying mechanism directly instead of through xdist:
two threads take the *same* advisory lock id, on separate connections built
the same way ``conftest.py`` builds its lock connection (schema-owner sync
URL, ``+psycopg`` driver marker stripped for ``psycopg.connect``), and one
must block until the other releases. If the lock were a no-op — the exact
failure mode ``bb0bbafc`` found and fixed (silently falling through to an
unprotected upgrade) — both threads would acquire immediately and the
intervals would overlap.
"""
from __future__ import annotations

import os
import threading
import time

import pytest


def _test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(
    not _test_database_url(), reason="TEST_DATABASE_URL not set — integration tier runs in CI"
)
def test_advisory_lock_serializes_two_concurrent_holders() -> None:
    import psycopg

    from app.config import settings

    # Same transform conftest.py's migrated_db._upgrade() applies: psycopg.connect()
    # speaks libpq conninfo, not the SQLAlchemy "postgresql+psycopg://" URL.
    lock_url = settings.migration_url_sync.replace("+psycopg", "", 1)
    lock_id = 1  # the exact id migrated_db._upgrade() takes.

    intervals: list[tuple[str, float, float]] = []
    lock = threading.Lock()
    holder_acquired = threading.Event()

    def hold_then_release(label: str, hold_seconds: float, wait_for: threading.Event | None) -> None:
        if wait_for is not None:
            assert wait_for.wait(timeout=10), f"{label}: never observed the other holder acquire"
        conn = psycopg.connect(lock_url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
                acquired_at = time.monotonic()
                if wait_for is None:
                    holder_acquired.set()
                time.sleep(hold_seconds)
                released_at = time.monotonic()
                cur.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
            with lock:
                intervals.append((label, acquired_at, released_at))
        finally:
            conn.close()

    first = threading.Thread(target=hold_then_release, args=("first", 1.0, None))
    second = threading.Thread(target=hold_then_release, args=("second", 0.0, holder_acquired))

    first.start()
    second.start()
    first.join(timeout=15)
    second.join(timeout=15)

    assert not first.is_alive() and not second.is_alive(), "a holder never finished — lock likely stuck"
    assert len(intervals) == 2

    by_label = {label: (acquired, released) for label, acquired, released in intervals}
    _first_acquired, first_released = by_label["first"]
    second_acquired, _ = by_label["second"]

    # The real proof: "second" only started waiting on the lock after "first" had
    # already acquired it (holder_acquired), so if the lock actually serializes,
    # "second" cannot acquire before "first" releases. A no-op lock (bb0bbafc's
    # bug) would let both acquire immediately, making this assertion false.
    assert second_acquired >= first_released, (
        "second holder acquired the advisory lock before the first released it — "
        "the lock is not actually serializing (this is the bb0bbafc no-op failure mode)"
    )
