"""(#154) _TTLCache — the bounded, lazily-expiring per-tenant Bot/Dispatcher cache.
A fake clock drives expiry deterministically (no real sleeps)."""
from __future__ import annotations

from uuid import uuid4

from app.api.bot import _TTLCache


def test_get_returns_set_value() -> None:
    c = _TTLCache(ttl=10, maxsize=4, clock=lambda: 0.0)
    k = uuid4()
    c.set(k, "v")
    assert c.get(k) == "v"
    assert c.get(uuid4()) is None  # unknown key


def test_entry_expires_after_ttl() -> None:
    now = {"t": 0.0}
    c = _TTLCache(ttl=10, maxsize=4, clock=lambda: now["t"])
    k = uuid4()
    c.set(k, "v")
    now["t"] = 9.9
    assert c.get(k) == "v"          # still fresh
    now["t"] = 10.1
    assert c.get(k) is None         # expired → dropped on access


def test_maxsize_evicts_oldest() -> None:
    c = _TTLCache(ttl=100, maxsize=2, clock=lambda: 0.0)
    a, b, d = uuid4(), uuid4(), uuid4()
    c.set(a, "A")
    c.set(b, "B")
    c.set(d, "D")                   # over capacity → evict oldest-inserted (a)
    assert c.get(a) is None
    assert c.get(b) == "B"
    assert c.get(d) == "D"


def test_updating_existing_key_does_not_evict() -> None:
    c = _TTLCache(ttl=100, maxsize=2, clock=lambda: 0.0)
    a, b = uuid4(), uuid4()
    c.set(a, "A")
    c.set(b, "B")
    c.set(a, "A2")                  # a already present → update in place, no eviction
    assert c.get(a) == "A2"
    assert c.get(b) == "B"


def test_pop_removes_and_tolerates_missing() -> None:
    c = _TTLCache(ttl=10, maxsize=4, clock=lambda: 0.0)
    k = uuid4()
    c.set(k, "v")
    c.pop(k)
    assert c.get(k) is None
    c.pop(uuid4())                  # missing key — must not raise
