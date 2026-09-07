"""Test suite for idempotency key mechanism (#751 fix).

Verifies that operations can be safely replayed without duplicate side effects,
essential for handling concurrent cancellations and webhook retries.
"""
from uuid import uuid4

import pytest

import app.core.idempotency as idem
from app.core.idempotency import (
    check_idempotency,
    idempotency_key,
    record_idempotency,
    with_idempotency,
)


class _FakeRedis:
    """In-memory async Redis stand-in (get/set/delete) — same pattern as
    test_cache.py / test_ratelimit.py. The real ``get_redis()`` opens a socket
    that isn't available in the unit run; without this mock, ``check``/``record``
    silently no-op (their Redis errors are swallowed) so every assertion here
    fails on ``None``. Synchronous (no internal await), which is also why the
    concurrency test runs the gathered coroutines to completion in turn."""

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


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    """Back the idempotency store with a fresh in-memory fake for every test."""
    fake = _FakeRedis()
    monkeypatch.setattr(idem, "get_redis", lambda: fake)
    return fake


class TestIdempotencyKey:
    """Test idempotency key generation."""

    @pytest.mark.asyncio
    async def test_key_format(self) -> None:
        """Idempotency key has correct format."""
        key = await idempotency_key(
            scope="subscription",
            operation="cancel",
            entity_id="123-456",
        )
        assert key == "subscription:cancel:123-456"

    @pytest.mark.asyncio
    async def test_key_consistency(self) -> None:
        """Same inputs produce same key."""
        key1 = await idempotency_key(
            scope="sub",
            operation="cancel",
            entity_id="abc",
        )
        key2 = await idempotency_key(
            scope="sub",
            operation="cancel",
            entity_id="abc",
        )
        assert key1 == key2

    @pytest.mark.asyncio
    async def test_key_differentiates_operations(self) -> None:
        """Different operations produce different keys."""
        cancel_key = await idempotency_key(
            scope="sub",
            operation="cancel",
            entity_id="123",
        )
        pause_key = await idempotency_key(
            scope="sub",
            operation="pause",
            entity_id="123",
        )
        assert cancel_key != pause_key


class TestIdempotencyStorage:
    """Test recording and checking idempotency."""

    @pytest.mark.asyncio
    async def test_check_missing_key(self) -> None:
        """Checking missing key returns None."""
        result = await check_idempotency("nonexistent:key:xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_record_and_check(self) -> None:
        """Recorded result can be retrieved."""
        key = "test:op:123"
        record = {"success": True, "cancelled_at": "2026-06-12T00:00:00Z"}

        await record_idempotency(key, record)
        result = await check_idempotency(key)

        assert result == record

    @pytest.mark.asyncio
    async def test_record_failure(self) -> None:
        """Recording failed operations."""
        key = "test:fail:456"
        record = {
            "success": False,
            "error": "subscription_already_cancelled",
        }

        await record_idempotency(key, record)
        result = await check_idempotency(key)

        assert result == record
        assert result["success"] is False


class TestIdempotencyWithOperation:
    """Test with_idempotency wrapper."""

    @pytest.mark.asyncio
    async def test_first_call_executes_operation(self) -> None:
        """First call executes the operation."""
        call_count = 0

        async def mock_operation() -> dict[str, bool]:
            nonlocal call_count
            call_count += 1
            return {"success": True, "calls": call_count}

        key = f"test:op:{uuid4()}"
        result = await with_idempotency(key, mock_operation)

        assert result["success"] is True
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_second_call_cached(self) -> None:
        """Second call with same key returns cached result."""
        call_count = 0

        async def mock_operation() -> dict[str, bool]:
            nonlocal call_count
            call_count += 1
            return {"success": True, "calls": call_count}

        key = f"test:cached:{uuid4()}"

        # First call
        result1 = await with_idempotency(key, mock_operation)
        assert result1["calls"] == 1

        # Second call (should be cached)
        result2 = await with_idempotency(key, mock_operation)
        assert result2["calls"] == 1  # Still shows 1 (cached)
        assert call_count == 1  # Operation only called once

    @pytest.mark.asyncio
    async def test_different_keys_separate_operations(self) -> None:
        """Different keys execute operation independently."""
        async def mock_operation() -> dict[str, int]:
            return {"value": 42}

        key1 = f"test:separate:{uuid4()}"
        key2 = f"test:separate:{uuid4()}"

        result1 = await with_idempotency(key1, mock_operation)
        result2 = await with_idempotency(key2, mock_operation)

        # Both return same result but were "executed" independently
        assert result1 == result2

    @pytest.mark.asyncio
    async def test_with_args_and_kwargs(self) -> None:
        """with_idempotency passes args and kwargs correctly."""
        async def mock_operation(a: int, b: int, *, factor: int = 1) -> dict[str, int]:
            return {"result": (a + b) * factor}

        key = f"test:args:{uuid4()}"
        result = await with_idempotency(
            key,
            mock_operation,
            2, 3,
            factor=10,
        )

        assert result["result"] == 50  # (2 + 3) * 10


class TestConcurrencyScenarios:
    """Test concurrent operation scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_cancellations(self) -> None:
        """Concurrent cancellations with same key both return same result."""
        call_count = 0

        async def cancel_subscription() -> dict[str, bool]:
            nonlocal call_count
            call_count += 1
            return {
                "success": True,
                "cancelled_at": "2026-06-12T00:00:00Z",
            }

        key = await idempotency_key(
            scope="subscription",
            operation="cancel",
            entity_id="sub-123",
        )

        # Simulate concurrent calls
        import asyncio
        results = await asyncio.gather(
            with_idempotency(key, cancel_subscription),
            with_idempotency(key, cancel_subscription),
            with_idempotency(key, cancel_subscription),
        )

        # All return same result
        assert all(r == results[0] for r in results)
        # Operation executed once (others got cached result)
        assert call_count == 1


class TestTenantIsolation:
    """Test tenant isolation in idempotency keys (#750)."""

    @pytest.mark.asyncio
    async def test_same_operation_different_tenants_different_keys(self) -> None:
        """Same operation on different tenants produces different keys."""
        key1 = await idempotency_key(
            scope="subscription",
            operation="cancel",
            entity_id="sub-123",
            tenant_id="tenant-1",
        )
        key2 = await idempotency_key(
            scope="subscription",
            operation="cancel",
            entity_id="sub-123",
            tenant_id="tenant-2",
        )
        assert key1 != key2
        assert "tenant-1" in key1
        assert "tenant-2" in key2

    @pytest.mark.asyncio
    async def test_key_format_with_tenant(self) -> None:
        """Key format includes tenant when provided."""
        key = await idempotency_key(
            scope="sub",
            operation="cancel",
            entity_id="123",
            tenant_id="tenant-abc",
        )
        assert key == "tenant-abc:sub:cancel:123"

    @pytest.mark.asyncio
    async def test_key_format_without_tenant(self) -> None:
        """Key format is unchanged when tenant_id is None."""
        key = await idempotency_key(
            scope="sub",
            operation="cancel",
            entity_id="123",
            tenant_id=None,
        )
        assert key == "sub:cancel:123"

    @pytest.mark.asyncio
    async def test_backward_compatibility_no_tenant(self) -> None:
        """Existing keys without tenant_id still work."""
        key = await idempotency_key(
            scope="payment",
            operation="process",
            entity_id="pay-456",
        )
        assert key == "payment:process:pay-456"
