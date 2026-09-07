"""(#256) Redis read-through cache helper — hit/miss + fail-open."""
from __future__ import annotations

import asyncio

import orjson

import app.core.cache as cache


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
        return len(keys)


class _DeadRedis:
    async def get(self, key):
        raise ConnectionError("redis down")

    async def set(self, *a, **k):
        raise ConnectionError("redis down")

    async def delete(self, *keys):
        raise ConnectionError("redis down")


def test_miss_then_hit_computes_once(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(cache, "get_redis", lambda: fake)
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return {"v": 42}

    async def run():
        a = await cache.cached_json("overview:t1", ttl=30, compute=compute, label="t")
        b = await cache.cached_json("overview:t1", ttl=30, compute=compute, label="t")
        assert a == b == {"v": 42}
        assert calls["n"] == 1                      # second call served from cache
        assert orjson.loads(fake.store["overview:t1"]) == {"v": 42}

    asyncio.run(run())


def test_distinct_keys_are_isolated(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(cache, "get_redis", lambda: fake)

    async def run():
        await cache.cached_json("overview:t1", 30, lambda: _const({"t": 1}), label="t")
        await cache.cached_json("overview:t2", 30, lambda: _const({"t": 2}), label="t")
        assert orjson.loads(fake.store["overview:t1"]) == {"t": 1}
        assert orjson.loads(fake.store["overview:t2"]) == {"t": 2}

    asyncio.run(run())


def test_fails_open_when_redis_down(monkeypatch):
    monkeypatch.setattr(cache, "get_redis", lambda: _DeadRedis())
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return {"ok": True}

    async def run():
        for _ in range(3):
            assert await cache.cached_json("k", 30, compute, label="t") == {"ok": True}
        assert calls["n"] == 3                       # no caching → computes every time, never raises

    asyncio.run(run())


def test_invalidate_removes_key(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(cache, "get_redis", lambda: fake)

    async def run():
        await cache.cached_json("k1", 30, lambda: _const(1), label="t")
        assert "k1" in fake.store
        await cache.invalidate("k1")
        assert "k1" not in fake.store

    asyncio.run(run())


async def _const(v):
    return v
