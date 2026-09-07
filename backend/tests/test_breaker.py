"""(#255) Circuit-breaker state machine — deterministic via an injectable clock."""
from __future__ import annotations

import asyncio

import pytest

from app.core.breaker import BreakerOpenError, CircuitBreaker


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_opens_after_threshold_consecutive_failures() -> None:
    clk = _Clock()
    cb = CircuitBreaker("t", failure_threshold=3, recovery_timeout=10, clock=clk)
    assert cb.state == "closed"
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"   # 2 < threshold
    cb.record_failure()
    assert cb.state == "open"     # 3rd trips
    assert cb.allow() is False


def test_a_success_resets_the_consecutive_counter() -> None:
    cb = CircuitBreaker("t", failure_threshold=3, recovery_timeout=10, clock=_Clock())
    cb.record_failure()
    cb.record_failure()
    cb.record_success()           # resets
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"   # never reached 3 consecutive


def test_half_opens_after_recovery_timeout_then_closes_on_success() -> None:
    clk = _Clock()
    cb = CircuitBreaker("t", failure_threshold=1, recovery_timeout=10, clock=clk)
    cb.record_failure()
    assert cb.state == "open"
    clk.t = 9.0
    assert cb.state == "open"     # cooldown not elapsed
    clk.t = 10.0
    assert cb.state == "half_open"  # trial permitted
    assert cb.allow() is True
    cb.record_success()
    assert cb.state == "closed"


def test_failure_in_half_open_reopens_immediately() -> None:
    clk = _Clock()
    cb = CircuitBreaker("t", failure_threshold=5, recovery_timeout=10, clock=clk)
    for _ in range(5):
        cb.record_failure()
    assert cb.state == "open"
    clk.t = 10.0
    assert cb.state == "half_open"
    cb.record_failure()           # trial failed → straight back to open
    assert cb.state == "open"


def test_call_raises_breakeropen_when_open_and_does_not_invoke_fn() -> None:
    cb = CircuitBreaker("t", failure_threshold=1, recovery_timeout=10, clock=_Clock())
    cb.record_failure()           # open

    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        return "ok"

    async def run():
        with pytest.raises(BreakerOpenError):
            await cb.call(fn)
        assert calls["n"] == 0     # short-circuited, fn never ran

    asyncio.run(run())


def test_call_counts_exceptions_and_reraises() -> None:
    cb = CircuitBreaker("t", failure_threshold=2, recovery_timeout=10, clock=_Clock())

    async def boom():
        raise ValueError("nope")

    async def ok():
        return 42

    async def run():
        with pytest.raises(ValueError):
            await cb.call(boom)
        assert cb.state == "closed"   # 1 < threshold
        with pytest.raises(ValueError):
            await cb.call(boom)
        assert cb.state == "open"     # 2nd failure trips
        # and a success path closes it again (after cooldown→half_open)
        cb._opened_at = -100          # force cooldown elapsed
        assert await cb.call(ok) == 42
        assert cb.state == "closed"

    asyncio.run(run())
