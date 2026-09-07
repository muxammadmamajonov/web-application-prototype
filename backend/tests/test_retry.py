"""(#149) retry_http retries idempotent outbound calls with backoff and gives up
after the attempt budget."""
from __future__ import annotations

import httpx
import pytest

from app.core.retry import retry_http


async def test_retries_then_succeeds() -> None:
    n = {"c": 0}

    async def flaky() -> str:
        n["c"] += 1
        if n["c"] < 3:
            raise httpx.ConnectError("boom")
        return "ok"

    assert await retry_http(flaky, attempts=3, base_delay=0) == "ok"
    assert n["c"] == 3


async def test_gives_up_after_attempts() -> None:
    n = {"c": 0}

    async def always_fail() -> str:
        n["c"] += 1
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.HTTPError):
        await retry_http(always_fail, attempts=3, base_delay=0)
    assert n["c"] == 3


async def test_no_retry_on_success() -> None:
    n = {"c": 0}

    async def ok() -> int:
        n["c"] += 1
        return 42

    assert await retry_http(ok, attempts=3, base_delay=0) == 42
    assert n["c"] == 1
