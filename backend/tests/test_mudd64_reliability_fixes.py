"""MUDD-64 — Stage 3 reliability remediation (H1, H2, H5).

H1: ``deliver_broadcast_bg`` / ``deliver_platform_broadcast_bg``
(``app/services/notifications.py``) loaded the whole audience in one query and
held every ``BroadcastRecipient`` row uncommitted until a single
``db.commit()`` at the very end. The ``app`` machine that runs this
background task has ``kill_timeout = "10s"`` and ``auto_stop_machines =
"suspend"`` (``ops/fly.toml``) — a deploy or scale-to-zero mid-broadcast lost
every send, committed or not, with no record of who was actually messaged.
The fix batches sends + commits (``_BROADCAST_BATCH``) and makes a re-invoked
delivery idempotent (anti-join against already-recorded recipients), so a
crash loses at most one in-flight batch and a resumed run never double-sends.

H2: Redis (``app/core/redis.py``) had neither a circuit breaker nor a
connection cap. A slow/unreachable Upstash meant every caller paid the full
5s ``socket_timeout`` before its own fail-open/fail-closed policy kicked in,
with nothing bounding how many connections piled up concurrently. The fix
wraps the shared client's dispatch in ``redis_breaker`` (``app/core/breaker.py``)
so a sustained outage fails fast instead of on every call, and caps
``max_connections``.

H5: ``load-tests/`` scripts claim measured baselines but hit routes/auth
schemes that don't exist in the current API (see the README warning added by
this same change). Guarded here by a source-level check that the disclaimer
survives.

Reproduces H1 against a real Postgres (``migrated_db``), matching the pattern
used by ``tests/test_mudd63_unlocked_money_races.py``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# ── H1: broadcast batching + crash durability + idempotent resume ──────────

async def _seed_broadcast_audience(engine, *, slug: str, n: int):
    """A tenant + ``n`` active customers (all with a telegram_id) + a draft
    broadcast targeting them."""
    from app.models import Broadcast, Customer, Tenant
    from app.models.enums import BroadcastStatus, BroadcastTarget, CustomerStatus

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug=slug, name="Broadcast T")
        s.add(t)
        await s.flush()
        customers = []
        for i in range(n):
            c = Customer(
                tenant_id=t.id, full_name=f"Cust {i}", phone=f"+99890000{i:04d}",
                status=CustomerStatus.active, telegram_id=1_000_000 + i,
            )
            s.add(c)
            customers.append(c)
        b = Broadcast(
            tenant_id=t.id, title="Sale", body_uz="50% off",
            target=BroadcastTarget.all, status=BroadcastStatus.draft,
        )
        s.add(b)
        await s.flush()
        await s.commit()
        return t, [c.id for c in customers], str(b.id)


def _patch_worker_session(monkeypatch, engine):
    """Point ``worker_session`` at the test engine.

    Both delivery functions do a late ``from app.db import worker_session``
    inside their own body (not a module-level import in notifications.py), so
    the source attribute on ``app.db`` — not anything on
    ``app.services.notifications`` — is what has to be patched for a fresh
    call to pick it up.
    """
    import app.db as db_mod
    real_worker_session = db_mod.worker_session

    def _worker_session_on_test_engine(**kwargs):
        return real_worker_session(
            factory=async_sessionmaker(engine, expire_on_commit=False), **kwargs,
        )

    monkeypatch.setattr(db_mod, "worker_session", _worker_session_on_test_engine)


async def test_deliver_broadcast_bg_batches_commits_and_survives_a_crash(
    migrated_db, monkeypatch,
) -> None:
    """5 recipients, batch size patched to 2 (3 batches). ``send_to_telegram``
    is patched to raise on the first customer of the 3rd batch — simulating the
    app machine being killed mid-send. The first two (committed) batches must
    survive; the third (uncommitted) batch must not."""
    import app.services.notifications as n

    engine = migrated_db
    t, _customer_ids, broadcast_id = await _seed_broadcast_audience(
        engine, slug="mudd64-crash", n=5,
    )
    _patch_worker_session(monkeypatch, engine)
    monkeypatch.setattr(n, "_BROADCAST_BATCH", 2)

    sent_to: list[int] = []

    async def _flaky_send(telegram_id, *args, **kwargs):
        # customer_ids are inserted in order, so id-keyset order == insertion
        # order here — the 5th call (index 4, the sole member of batch 3)
        # simulates the kill.
        if len(sent_to) == 4:
            raise RuntimeError("simulated app-machine kill mid-send")
        sent_to.append(telegram_id)
        return True

    monkeypatch.setattr(n, "send_to_telegram", _flaky_send)

    await n.deliver_broadcast_bg(broadcast_id=broadcast_id, tenant_id=t.id)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        from app.models import Broadcast, BroadcastRecipient

        rows = (await s.execute(
            select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id)
        )).scalars().all()
        # Batches 1+2 (4 customers) committed; batch 3's single row did not.
        assert len(rows) == 4, "the two committed batches must survive the crash"
        assert {r.status for r in rows} == {"sent"}

        b = (await s.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        )).scalar_one()
        # The crash happened before the final aggregate/finalize step ran.
        assert b.status.value == "draft"
        assert b.finished_at is None

    # ── resume: re-invoke for the same broadcast, now with a healthy send ──
    async def _healthy_send(telegram_id, *args, **kwargs):
        sent_to.append(telegram_id)
        return True

    monkeypatch.setattr(n, "send_to_telegram", _healthy_send)
    calls_before_resume = len(sent_to)

    await n.deliver_broadcast_bg(broadcast_id=broadcast_id, tenant_id=t.id)

    async with Session() as s:
        from app.models import Broadcast, BroadcastRecipient

        rows = (await s.execute(
            select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id)
        )).scalars().all()
        assert len(rows) == 5, "resume must cover the whole audience, no more no less"
        by_customer = [r.customer_id for r in rows]
        assert len(by_customer) == len(set(by_customer)), (
            "resume must not create a duplicate recipient row for anyone "
            "already recorded before the crash"
        )
        assert {r.status for r in rows} == {"sent"}

        b = (await s.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        )).scalar_one()
        assert b.sent_count == 5
        assert b.failed_count == 0
        assert b.status.value == "sent"
        assert b.finished_at is not None

    # Only the still-pending customer (1) got a NEW send on resume — the 4
    # already-recorded customers were not re-sent to.
    assert len(sent_to) - calls_before_resume == 1, (
        "resume must only send to recipients that weren't already recorded"
    )


async def test_deliver_platform_broadcast_bg_resume_does_not_duplicate(
    migrated_db, monkeypatch,
) -> None:
    """Same crash-then-resume contract for the platform (HQ) broadcast path,
    which resolves recipients via ``user_segments`` instead of a Customer
    query."""
    from app.models import Broadcast, User
    from app.models.enums import BroadcastStatus

    engine = migrated_db
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        users = []
        for i in range(4):
            u = User(telegram_id=2_000_000 + i)
            s.add(u)
            users.append(u)
        b = Broadcast(
            tenant_id=None, title="HQ update", body_uz="New feature",
            status=BroadcastStatus.draft, target_filter={"segment": "owners"},
        )
        s.add(b)
        await s.flush()
        broadcast_id = str(b.id)
        await s.commit()

    import app.services.notifications as n
    _patch_worker_session(monkeypatch, engine)
    monkeypatch.setattr(n, "_BROADCAST_BATCH", 2)

    async def _all_owners(db, segment):
        return [(2_000_000 + i, "uz") for i in range(4)]

    # deliver_platform_broadcast_bg does a late `from app.services.user_segments
    # import recipients_with_locale` inside the function body, so the source
    # module's attribute — not notifications.py's — is what must be patched.
    import app.services.user_segments as seg
    monkeypatch.setattr(seg, "recipients_with_locale", _all_owners)

    sent_to: list[int] = []

    async def _flaky_send(telegram_id, *args, **kwargs):
        if len(sent_to) == 2:  # crash partway through the 2nd batch
            raise RuntimeError("simulated kill")
        sent_to.append(telegram_id)
        return True

    monkeypatch.setattr(n, "send_to_telegram", _flaky_send)
    await n.deliver_platform_broadcast_bg(broadcast_id=broadcast_id)

    async with Session() as s:
        from app.models import BroadcastRecipient

        rows = (await s.execute(
            select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id)
        )).scalars().all()
        assert len(rows) == 2, "the first (committed) batch must survive"

    async def _healthy_send(telegram_id, *args, **kwargs):
        sent_to.append(telegram_id)
        return True

    monkeypatch.setattr(n, "send_to_telegram", _healthy_send)
    await n.deliver_platform_broadcast_bg(broadcast_id=broadcast_id)

    async with Session() as s:
        from app.models import Broadcast, BroadcastRecipient

        rows = (await s.execute(
            select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id)
        )).scalars().all()
        assert len(rows) == 4
        by_tg = [r.telegram_id for r in rows]
        assert len(by_tg) == len(set(by_tg)), "resume must not double-record a recipient"

        b = (await s.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        )).scalar_one()
        assert b.sent_count == 4
        assert b.status.value == "sent"


def test_broadcast_batch_size_is_defined_and_bounded() -> None:
    import app.services.notifications as n

    assert 0 < n._BROADCAST_BATCH <= 1000, "batch size must bound memory/commit granularity"


# ── H2: Redis circuit breaker + bounded pool ────────────────────────────────

def test_redis_breaker_is_registered() -> None:
    from app.core.breaker import CircuitBreaker, redis_breaker

    assert isinstance(redis_breaker, CircuitBreaker)
    assert redis_breaker.name == "redis"
    # Low threshold: Redis sits on nearly every request, so a few consecutive
    # failures is already strong signal of a real outage, not noise.
    assert redis_breaker.failure_threshold <= 10


async def test_breaker_redis_fails_fast_once_open_without_touching_the_socket() -> None:
    """Once ``redis_breaker`` trips, ``_BreakerRedis`` must raise
    ``BreakerOpenError`` *before* delegating to the real ``execute_command`` —
    the whole point is skipping the 5s ``socket_timeout`` wait during an
    outage, not just wrapping it."""
    import contextlib

    from redis.asyncio import Redis

    from app.core.breaker import BreakerOpenError, redis_breaker
    from app.core.redis import _BreakerRedis

    # `redis_breaker` is process-global singleton state — some earlier test in
    # the full suite may have left it open (or mid-count). Start from a known
    # closed state so this test's own failure count is what trips it.
    redis_breaker.record_success()

    client = _BreakerRedis(host="127.0.0.1", port=1)  # nothing listens here

    real_calls = 0

    async def _boom(self, *args, **kwargs):
        nonlocal real_calls
        real_calls += 1
        raise ConnectionError("simulated redis outage")

    import unittest.mock as mock
    with mock.patch.object(Redis, "execute_command", _boom):
        for _ in range(redis_breaker.failure_threshold):
            with contextlib.suppress(ConnectionError):
                await client.ping()
        assert redis_breaker.state == "open"
        calls_at_trip = real_calls

        try:
            await client.ping()
        except BreakerOpenError:
            pass
        else:
            raise AssertionError("expected BreakerOpenError once the breaker is open")
        # The open-circuit call must not have reached the real transport.
        assert real_calls == calls_at_trip

    redis_breaker.record_success()  # reset shared module state for other tests


async def test_breaker_redis_records_success_and_stays_closed_on_a_healthy_call() -> None:
    from redis.asyncio import Redis

    from app.core.breaker import redis_breaker
    from app.core.redis import _BreakerRedis

    # Same process-global-state concern as the test above: force a known
    # CLOSED baseline before deliberately dirtying it below, so an open
    # breaker left by an unrelated earlier test can't fail this one via
    # BreakerOpenError instead of exercising the healthy-call path.
    redis_breaker.record_success()

    client = _BreakerRedis(host="127.0.0.1", port=1)

    import unittest.mock as mock
    with mock.patch.object(Redis, "execute_command", mock.AsyncMock(return_value="PONG")):
        redis_breaker.record_failure()  # start from a dirty count
        result = await client.ping()
        assert result == "PONG"
        assert redis_breaker.state == "closed"


def test_get_redis_client_has_a_bounded_connection_pool() -> None:
    import importlib

    import app.core.redis as redis_mod
    importlib.reload(redis_mod)
    try:
        client = redis_mod.get_redis()
        assert client.connection_pool.max_connections == redis_mod._MAX_CONNECTIONS
        assert redis_mod._MAX_CONNECTIONS < 10_000, "must actually bound the pool, not just document a huge number"
    finally:
        asyncio.run(redis_mod.close_redis())


def test_get_redis_returns_the_breaker_wrapped_client() -> None:
    from app.core.redis import _BreakerRedis, get_redis

    assert isinstance(get_redis(), _BreakerRedis)


# ── H5: load-tests README carries an accurate disclaimer ───────────────────

def test_load_tests_readme_discloses_scripts_dont_hit_real_routes() -> None:
    readme = Path(__file__).resolve().parents[2] / "load-tests" / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "ever measured a real baseline" in text
    # Names the concrete mismatch, not just a vague caveat — an operator
    # reading this must be able to tell WHY the numbers aren't real.
    assert "/tenant/payments" in text
    assert "worker_authority" in text or "not HTTP endpoints" in text
