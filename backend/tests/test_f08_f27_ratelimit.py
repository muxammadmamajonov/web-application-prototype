"""F-08 + F-27 — rate-limiter trusted-IP resolution and atomic incr/expire.

F-08: ``_client_ip`` resolves the real end-user IP from the CF Pages proxy's
``X-Real-Client-IP`` — trusted ONLY when ``X-Proxy-Secret`` matches the shared
secret, so a direct-to-Fly request can't forge it. Behind the proxy ``Fly-Client-IP``
is Cloudflare's *shared* egress (same for every user), so it is only a fallback;
keying on it collapses all users into one bucket and globally rate-limits login.

F-27: the per-request increment must be atomic. The limiter now runs INCR + the
first-hit EXPIRE inside a single Redis ``EVAL`` (Lua), so a crash can't leave a
key without a TTL (a permanent counter). These tests drive the real ``eval``
code path and assert the window TTL is always set on the first hit.

Follows the hand-rolled-fake + ``asyncio.run`` style of tests/test_ratelimit.py
(no fakeredis / pytest-asyncio dependency).
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import app.core.ratelimit as rl


class _Req:
    def __init__(self, headers=None, ip="1.2.3.4"):
        self.headers = headers or {}
        self.client = type("C", (), {"host": ip})()


# ── F-08: trusted-header precedence in _client_ip ────────────────────────────

def test_fly_client_ip_beats_spoofed_xff():
    # Attacker spoofs X-Forwarded-For; Fly's trusted header must win.
    req = _Req(headers={
        "x-forwarded-for": "6.6.6.6",          # attacker-controlled
        "fly-client-ip": "203.0.113.9",        # stamped by Fly's proxy
    })
    assert rl._client_ip(req) == "203.0.113.9"


def test_verified_proxy_real_ip_wins(monkeypatch):
    # The CF Pages proxy forwards the real end-user IP + the shared secret. With a
    # matching secret it must win over Fly-Client-IP (which is CF's shared egress).
    monkeypatch.setattr(rl.settings, "proxy_shared_secret", "shh")
    req = _Req(headers={
        "x-real-client-ip": "198.51.100.7",    # real user, relayed by our proxy
        "x-proxy-secret": "shh",               # proves it came through the proxy
        "fly-client-ip": "203.0.113.9",        # CF shared egress — must NOT win
        "x-forwarded-for": "6.6.6.6",
    })
    assert rl._client_ip(req) == "198.51.100.7"


def test_real_ip_ignored_without_matching_secret(monkeypatch):
    # A request reaching Fly directly can set x-real-client-ip but not the secret →
    # it must be ignored (no spoofing), falling back to the real socket source.
    monkeypatch.setattr(rl.settings, "proxy_shared_secret", "shh")
    req = _Req(headers={
        "x-real-client-ip": "6.6.6.6",         # attacker-supplied, no valid secret
        "x-proxy-secret": "wrong",
        "fly-client-ip": "203.0.113.9",
    })
    assert rl._client_ip(req) == "203.0.113.9"


def test_real_ip_ignored_when_secret_unset(monkeypatch):
    # With no configured proxy secret, the forwarded IP header is never trusted.
    monkeypatch.setattr(rl.settings, "proxy_shared_secret", None)
    req = _Req(headers={"x-real-client-ip": "6.6.6.6", "fly-client-ip": "203.0.113.9"})
    assert rl._client_ip(req) == "203.0.113.9"


def test_xff_fallback_uses_last_appended_hop():
    # No trusted header → fall back to XFF, but take the LAST hop (added by the
    # nearest trusted proxy), never the first (client-supplied) value.
    req = _Req(headers={"x-forwarded-for": "6.6.6.6, 10.0.0.1, 172.16.0.5"})
    assert rl._client_ip(req) == "172.16.0.5"


def test_xff_single_value_fallback():
    req = _Req(headers={"x-forwarded-for": "172.16.0.5"})
    assert rl._client_ip(req) == "172.16.0.5"


def test_falls_back_to_socket_peer_without_headers():
    assert rl._client_ip(_Req(ip="9.9.9.9")) == "9.9.9.9"


def test_identity_prefers_trusted_header_over_spoofed_xff():
    # End-to-end through _identity (the key the limiter actually buckets on):
    # a rotating X-Forwarded-For can no longer mint fresh identities while Fly
    # reports a stable client.
    req = _Req(headers={"x-forwarded-for": "6.6.6.6", "fly-client-ip": "203.0.113.9"})
    assert rl._identity(req) == "ip:203.0.113.9"


# ── F-27: atomic incr+expire via a single EVAL ──────────────────────────────

class _LuaRedis:
    """Redis stub that ONLY implements ``eval`` (the atomic path).

    It interprets the limiter's INCR+EXPIRE Lua well enough to track counts and
    record every TTL set, so a test can prove the window TTL is established on
    the first hit and never left unset. If the limiter regressed to calling
    bare ``incr``/``expire`` this fake would raise AttributeError and the test
    would fail loudly.
    """

    def __init__(self):
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.eval_calls = 0

    async def eval(self, script, numkeys, *keys_and_args):
        self.eval_calls += 1
        assert "INCR" in script and "EXPIRE" in script
        key = keys_and_args[0]
        window = int(keys_and_args[numkeys])  # first ARGV after the keys
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.counts[key] == 1:
            self.ttls[key] = window  # EXPIRE happens atomically with the INCR
        return self.counts[key]


def test_limiter_uses_eval_and_blocks_after_limit(monkeypatch):
    fake = _LuaRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    dep = rl.rate_limit("t", limit=3, window_seconds=60)
    req = _Req(headers={"fly-client-ip": "203.0.113.9"})

    async def run():
        for _ in range(3):
            await dep(req)  # 1..3 allowed
        with pytest.raises(HTTPException) as ei:
            await dep(req)  # 4th blocked
        assert ei.value.status_code == 429

    asyncio.run(run())
    # Every call went through the atomic EVAL path, not a bare incr/expire.
    assert fake.eval_calls == 4


def test_ttl_set_on_first_hit_no_permanent_counter(monkeypatch):
    # The crux of F-27: the TTL must be established on the very first increment,
    # in the same atomic op — never a key left without an expiry.
    fake = _LuaRedis()
    monkeypatch.setattr(rl, "get_redis", lambda: fake)
    dep = rl.rate_limit("t", limit=5, window_seconds=42)
    req = _Req(headers={"fly-client-ip": "203.0.113.9"})

    async def run():
        await dep(req)

    asyncio.run(run())
    key = "rl:t:ip:203.0.113.9"
    assert fake.ttls.get(key) == 42, "window TTL must be set atomically on first hit"


def test_incr_window_helper_is_atomic_single_round_trip():
    # Unit-test the helper directly: one EVAL, correct count, TTL on first hit.
    fake = _LuaRedis()

    async def run():
        assert await rl._incr_window(fake, "k", 30) == 1
        assert await rl._incr_window(fake, "k", 30) == 2

    asyncio.run(run())
    assert fake.eval_calls == 2
    assert fake.ttls == {"k": 30}


class _NoEvalRedis:
    """Backward-compat stub WITHOUT ``eval`` (mirrors tests/test_ratelimit.py's
    minimal fake). The helper must transparently fall back to incr+expire so the
    existing limiter tests stay green — the fallback is a capability shim, not an
    error-swallow."""

    def __init__(self):
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True


def test_incr_window_falls_back_when_no_eval():
    fake = _NoEvalRedis()

    async def run():
        assert await rl._incr_window(fake, "k", 30) == 1
        assert await rl._incr_window(fake, "k", 30) == 2

    asyncio.run(run())
    # First hit still sets the TTL via the legacy two-call path.
    assert fake.ttls == {"k": 30}


def test_fail_closed_preserved_when_redis_down(monkeypatch):
    # Regression guard: F-27's atomic rewrite must not change the fail policy.
    # An auth-style bucket (fail_closed=True) still refuses with 503 when the
    # backend is unreachable (eval raises).
    class _DeadRedis:
        async def eval(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(rl, "get_redis", lambda: _DeadRedis())
    dep = rl.rate_limit("auth", limit=30, window_seconds=60, fail_closed=True)

    async def run():
        with pytest.raises(HTTPException) as ei:
            await dep(_Req(headers={"fly-client-ip": "203.0.113.9"}))
        assert ei.value.status_code == 503

    asyncio.run(run())


def test_fail_open_preserved_when_redis_down(monkeypatch):
    # The default (money/public paths) still fails OPEN after the rewrite.
    class _DeadRedis:
        async def eval(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(rl, "get_redis", lambda: _DeadRedis())
    dep = rl.rate_limit("pay_init", limit=1, window_seconds=60)

    async def run():
        for _ in range(5):
            await dep(_Req(headers={"fly-client-ip": "203.0.113.9"}))  # never raises

    asyncio.run(run())
