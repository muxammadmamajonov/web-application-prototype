"""H1 — rate limiter: blocks over-limit, fails open when Redis is down."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import app.core.ratelimit as rl


class _Req:
    def __init__(self, headers=None, ip="1.2.3.4"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": ip})()


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}

    async def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        return True


class _DeadRedis:
    async def incr(self, key):
        raise ConnectionError("redis down")

    async def expire(self, key, ttl):
        raise ConnectionError("redis down")


def test_blocks_after_limit(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    dep = rl.rate_limit("t", limit=3, window_seconds=60)
    req = _Req()

    async def run():
        for _ in range(3):
            await dep(req)  # 1..3 allowed
        with pytest.raises(HTTPException) as ei:
            await dep(req)  # 4th blocked
        assert ei.value.status_code == 429

    asyncio.run(run())


def test_fails_open_when_redis_down(monkeypatch):
    monkeypatch.setattr(rl, "get_redis", lambda: _DeadRedis())
    dep = rl.rate_limit("t", limit=1, window_seconds=60)  # default fail_closed=False

    async def run():
        for _ in range(10):
            await dep(_Req())  # never raises — fail open

    asyncio.run(run())


def test_fail_closed_bucket_denies_when_redis_down(monkeypatch):
    # (#258) Abuse-sensitive buckets refuse with 503 rather than silently
    # dropping protection when the limiter backend is unreachable.
    monkeypatch.setattr(rl, "get_redis", lambda: _DeadRedis())
    dep = rl.rate_limit("auth", limit=30, window_seconds=60, fail_closed=True)

    async def run():
        with pytest.raises(HTTPException) as ei:
            await dep(_Req())
        assert ei.value.status_code == 503
        assert "Retry-After" in ei.value.headers

    asyncio.run(run())


def test_login_and_signup_are_fail_closed():
    # The shipped auth/signup limiters must be fail-closed; payment-init must not.
    import inspect
    assert "fail_closed=True" in inspect.getsource(rl.login_limit)
    assert "fail_closed=True" in inspect.getsource(rl.signup_limit)
    assert "fail_closed=True" not in inspect.getsource(rl.payment_init_limit)


# ── #756: /provision-trial is rate-limited (was a DoS vector) ─────────────────

def test_provision_trial_limit_blocks_after_limit(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    dep = rl.provision_trial_limit().dependency  # unwrap Depends → the callable
    req = _Req()

    async def run():
        for _ in range(10):
            await dep(req)  # 1..10 allowed (limit=10/hour)
        with pytest.raises(HTTPException) as ei:
            await dep(req)  # 11th blocked
        assert ei.value.status_code == 429

    asyncio.run(run())


def test_provision_trial_limit_is_fail_open():
    # Authenticated (behind current_user), so unlike signup/login it must fail
    # OPEN — a Redis blip can't be used to brute-force it, and failing closed
    # would block legitimate trial onboarding during a limiter outage.
    import inspect
    assert "fail_closed=True" not in inspect.getsource(rl.provision_trial_limit)


def test_provision_trial_route_is_wired_to_limiter():
    """Regression guard for #756: the limiter must be attached to the live route,
    not merely defined. Introspect the solved FastAPI dependant for a dependency
    originating in app.core.ratelimit."""
    from app.main import app

    route = next(
        r for r in app.routes
        if getattr(r, "path", None) == "/api/auth/provision-trial"
        and "POST" in getattr(r, "methods", set())
    )
    has_limiter = any(
        getattr(d.call, "__module__", "") == "app.core.ratelimit"
        for d in route.dependant.dependencies
    )
    assert has_limiter, "/provision-trial has no rate-limit dependency (#756)"


def test_identity_prefers_ip_for_anonymous():
    assert rl._identity(_Req(ip="9.9.9.9")) == "ip:9.9.9.9"


def test_identity_uses_xff_last_hop():
    # (#258 / F-08) When falling back to X-Forwarded-For, trust the LAST
    # (proxy-appended) hop, not the first (client-supplied) one — the first hop
    # is attacker-controllable and trusting it was the rate-limit-bypass bug.
    req = _Req(headers={"x-forwarded-for": "5.5.5.5, 10.0.0.1"})
    assert rl._identity(req) == "ip:10.0.0.1"
