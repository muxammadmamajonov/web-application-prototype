"""The heartbeat must stay SILENT whenever it cannot honestly claim health.

The whole value of a dead-man's switch is inverted: a normal monitor is wrong
when it fails to fire, this one is wrong when it fires when it shouldn't. A
heartbeat that pings while the database is unreachable is worse than no
heartbeat — it actively reassures the on-call that a broken system is fine.

So the assertions that matter most here are the ones about NOT sending.
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

import pytest

from app.services import heartbeat as hb

URL = "https://hc.example.test/ping/abc123"


class _SpyClient:
    """Stands in for httpx.AsyncClient and records every GET it is asked for."""

    # ClassVar: the spy is re-instantiated per call, so the record has to
    # outlive the instance to be assertable.
    calls: ClassVar[list[str]] = []
    last_timeout: ClassVar[float | None] = None

    def __init__(self, *_: Any, **kwargs: Any) -> None:
        self.timeout = kwargs.get("timeout")
        type(self).last_timeout = self.timeout

    async def __aenter__(self) -> _SpyClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def get(self, url: str) -> Any:
        type(self).calls.append(url)

        class _R:
            status_code = 200

        return _R()


@pytest.fixture(autouse=True)
def _reset() -> None:
    _SpyClient.calls = []


def _install(monkeypatch: pytest.MonkeyPatch, *, url: str | None, readiness: Any) -> None:
    monkeypatch.setattr(hb.settings, "heartbeat_url", url, raising=False)
    monkeypatch.setattr(hb, "readiness", readiness)
    monkeypatch.setattr(hb.httpx, "AsyncClient", _SpyClient)


async def _ready_ok() -> tuple[bool, dict[str, str]]:
    return True, {"db": "ok", "redis": "ok"}


async def _ready_db_down() -> tuple[bool, dict[str, str]]:
    return False, {"db": "down", "redis": "ok"}


async def _ready_redis_down() -> tuple[bool, dict[str, str]]:
    # readiness() treats Redis as degraded-but-servable on purpose.
    return True, {"db": "ok", "redis": "down"}


async def _ready_raises() -> tuple[bool, dict[str, str]]:
    raise RuntimeError("readiness itself exploded")


@pytest.mark.asyncio
async def test_pings_when_the_process_can_actually_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, url=URL, readiness=_ready_ok)
    await hb.emit_heartbeat_job()
    assert _SpyClient.calls == [URL]


@pytest.mark.asyncio
async def test_withholds_the_ping_when_the_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """THE core invariant. A machine that is up but cannot reach Postgres cannot
    take a payment, so it must trip the switch rather than hold it open."""
    _install(monkeypatch, url=URL, readiness=_ready_db_down)
    with caplog.at_level(logging.ERROR, logger="muddato.heartbeat"):
        await hb.emit_heartbeat_job()
    assert _SpyClient.calls == [], "pinged while unable to serve — the switch is a lie"
    assert any("withheld_not_ready" in r.message for r in caplog.records), (
        "withholding must be loud in the log; a silent withhold is indistinguishable "
        "from the job never running"
    )


@pytest.mark.asyncio
async def test_withholds_the_ping_when_the_readiness_check_itself_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown health is not health. Fail closed, same as everywhere else."""
    _install(monkeypatch, url=URL, readiness=_ready_raises)
    await hb.emit_heartbeat_job()
    assert _SpyClient.calls == []


@pytest.mark.asyncio
async def test_still_pings_when_only_redis_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Must agree with `readiness()`, which keeps serving on a Redis blip. If
    this tripped the switch on degraded Redis it would page on cache flaps."""
    _install(monkeypatch, url=URL, readiness=_ready_redis_down)
    await hb.emit_heartbeat_job()
    assert _SpyClient.calls == [URL]


@pytest.mark.asyncio
async def test_unconfigured_makes_no_network_call_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local dev, CI and review apps must never hold a production switch open."""
    _install(monkeypatch, url=None, readiness=_ready_ok)
    await hb.emit_heartbeat_job()
    assert _SpyClient.calls == []


@pytest.mark.asyncio
async def test_a_failed_send_never_escapes_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A monitoring feature that can crash the scheduler takes down the cron
    tier it rides on — dunning, late fees, review-notice retirement."""
    class _Boom(_SpyClient):
        async def get(self, url: str) -> Any:
            raise OSError("network is unreachable")

    monkeypatch.setattr(hb.settings, "heartbeat_url", URL, raising=False)
    monkeypatch.setattr(hb, "readiness", _ready_ok)
    monkeypatch.setattr(hb.httpx, "AsyncClient", _Boom)
    await hb.emit_heartbeat_job()  # must not raise


@pytest.mark.asyncio
async def test_the_client_is_given_a_short_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unbounded call to a third-party endpoint can pin a scheduler worker."""
    _install(monkeypatch, url=URL, readiness=_ready_ok)
    await hb.emit_heartbeat_job()
    assert _SpyClient.last_timeout is not None
    assert _SpyClient.last_timeout <= 10, "timeout too loose to protect the scheduler"
