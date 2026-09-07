"""(#148) The webhook update-dedup helper: claim each Telegram update_id once
(Redis SET NX) so at-least-once retries aren't reprocessed, and fail OPEN if Redis
is down so updates are never dropped."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.api.bot import _claim_update


def _redis_with_set(*returns) -> MagicMock:
    client = MagicMock()
    client.set = AsyncMock(side_effect=list(returns))
    return client


async def test_first_claim_processes_then_duplicate_skips() -> None:
    # SET NX returns truthy when newly set, None on a duplicate.
    client = _redis_with_set(True, None)
    with patch("app.core.redis.get_redis", return_value=client):
        assert await _claim_update("platform", 100) is True   # first delivery
        assert await _claim_update("platform", 100) is False  # Telegram retry


async def test_missing_update_id_processes() -> None:
    assert await _claim_update("platform", None) is True


async def test_redis_down_fails_open() -> None:
    client = MagicMock()
    client.set = AsyncMock(side_effect=RuntimeError("redis down"))
    with patch("app.core.redis.get_redis", return_value=client):
        assert await _claim_update("tenant-x", 5) is True
