"""Async circuit breaker for outbound dependencies (#255).

``retry.py`` rides out a transient blip; this **sheds load** when a dependency is
down for real. After ``failure_threshold`` consecutive failures the breaker trips
**OPEN** and calls fail fast — no piled-up retries/timeouts exhausting the worker
+ DB pool — until ``recovery_timeout`` elapses, when a single **HALF_OPEN** trial
decides whether to close again.

Two integration styles:

* ``await breaker.call(fn)`` — for callables that **raise** on failure (e.g. the
  archive.org upload wrapped in ``retry_http``). Raises ``BreakerOpenError`` when
  open.
* ``breaker.allow()`` + ``record_success()`` / ``record_failure()`` — for
  callables that return a **status** instead of raising (``send_sms`` →
  ``SmsResult``, ``send_to_telegram`` → ``bool``), so the caller decides which
  outcomes count as a dependency failure (e.g. a network timeout) vs a
  per-recipient error (e.g. a blocked bot) that must NOT trip the breaker.

Single event loop ⇒ no locking needed.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

log = logging.getLogger("muddato.breaker")

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


class BreakerOpenError(RuntimeError):
    """Raised by ``CircuitBreaker.call`` when the circuit is open."""

    def __init__(self, name: str) -> None:
        super().__init__(f"circuit breaker '{name}' is open")
        self.name = name


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._clock = clock
        self._failures = 0
        self._opened_at = 0.0
        self._state = CLOSED

    @property
    def state(self) -> str:
        """Current state, lazily transitioning OPEN→HALF_OPEN once the cooldown
        has elapsed (so a trial call is permitted)."""
        if self._state == OPEN and self._clock() - self._opened_at >= self.recovery_timeout:
            self._set_state(HALF_OPEN)
        return self._state

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            try:
                from app.core.metrics import record_breaker_state
                record_breaker_state(self.name, state)
            except Exception:
                # Best-effort instrumentation — the breaker's own state has already
                # changed above and must not depend on Prometheus. Debug, not warning:
                # a broken metrics backend would otherwise log on every transition.
                log.debug(
                    "breaker.metrics.state_record_failed name=%s state=%s",
                    self.name, state, exc_info=True,
                )

    def allow(self) -> bool:
        """True if a call may proceed (CLOSED, or a HALF_OPEN trial); False when OPEN."""
        return self.state != OPEN

    def record_success(self) -> None:
        self._failures = 0
        if self._state != CLOSED:
            log.info("breaker.%s recovered → closed", self.name)
        self._set_state(CLOSED)

    def record_failure(self) -> None:
        self._failures += 1
        if self._state == HALF_OPEN or self._failures >= self.failure_threshold:
            was_open = self._state == OPEN
            self._opened_at = self._clock()
            self._set_state(OPEN)
            if not was_open:
                log.warning("breaker.%s OPEN after %d failure(s)", self.name, self._failures)
                try:
                    from app.core.metrics import record_breaker_trip
                    record_breaker_trip(self.name)
                except Exception:
                    # The trip is already recorded in state + the warning above;
                    # losing the counter must not affect the circuit.
                    log.debug(
                        "breaker.metrics.trip_record_failed name=%s", self.name, exc_info=True,
                    )

    async def call[T](self, fn: Callable[[], Awaitable[T]]) -> T:
        """Run ``fn`` under the breaker. Any exception counts as a failure and is
        re-raised; success closes the breaker. Raises ``BreakerOpenError`` fast
        when the circuit is open."""
        if not self.allow():
            raise BreakerOpenError(self.name)
        try:
            result = await fn()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


# Named breakers for the app's outbound dependencies. Thresholds are tuned per
# dependency: notifications (SMS/Telegram) are best-effort and high-volume, so a
# few interspersed failures (a blocked recipient) must not trip them — only a
# SUSTAINED run of consecutive failures (a real provider outage) does.
archive_breaker = CircuitBreaker("archive_upload", failure_threshold=5, recovery_timeout=60.0)
sms_breaker = CircuitBreaker("eskiz_sms", failure_threshold=8, recovery_timeout=60.0)
telegram_breaker = CircuitBreaker("telegram_send", failure_threshold=20, recovery_timeout=30.0)
# (#H2) Redis (rate limiting, idempotency, cache, OIDC state, SMS dedup) sits on
# nearly every request path via `get_redis()`. Without a breaker, a slow/down
# Upstash means every one of those requests pays the full 5s socket_timeout
# before its caller's own fail-open/fail-closed handling kicks in — under
# concurrent load that ties up the whole app, not just the limiter. A low
# threshold + short recovery: trip fast (few consecutive failures, this
# dependency is called on nearly every request so 5 is plenty of signal),
# recheck often (15s) since Redis outages are typically brief blips, not the
# multi-minute affairs the SMS/archive breakers are tuned for.
redis_breaker = CircuitBreaker("redis", failure_threshold=5, recovery_timeout=15.0)
