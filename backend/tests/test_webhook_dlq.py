"""Webhook dead-letter-queue durability over a REAL Postgres (#754 fix).

The DLQ guards money-notification durability: a webhook that fails with a
*transient* error (timeout, 502) must be parked in ``webhook_dlq`` for replay
rather than silently dropped, while a *permanent* error (bad signature) is
refused without parking. Verifies the model, ``move_to_dlq``, the
classify-and-handle path (``handle_webhook_error``), and the ``get_dlq_depth``
monitoring gauge against the real schema.

The error-classification tests are pure unit checks (no DB). The DB-backed tests
use the ``migrated_db`` fixture and SKIP cleanly (never error) without a reachable
test Postgres — the standard ``TEST_DATABASE_URL`` integration gate (set in CI;
locally export ``TEST_DATABASE_URL``).
"""
from __future__ import annotations

import pytest

from app.core.webhook_error import (
    PermanentWebhookError,
    TransientWebhookError,
    handle_webhook_error,
)
from app.core.webhook_retry import MAX_WEBHOOK_RETRIES, get_dlq_depth, move_to_dlq
from app.models import WebhookDLQ


class TestWebhookDLQModel:
    """Test WebhookDLQ model creation and storage."""

    @pytest.mark.asyncio
    async def test_webhook_dlq_creation(self, migrated_db) -> None:
        """WebhookDLQ row can be created with all fields and retrieved."""
        from datetime import UTC, datetime

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession

        from app.models import Tenant

        async with AsyncSession(migrated_db, expire_on_commit=False) as db:
            # tenant_id carries a real FK (ON DELETE SET NULL) → seed a tenant.
            tenant = Tenant(slug="dlq-model-t", name="DLQ Model T")
            db.add(tenant)
            await db.flush()
            tenant_id = tenant.id
            payload = {"click_trans_id": "123", "amount": "100000"}

            dlq = WebhookDLQ(
                tenant_id=tenant_id,
                provider="click",
                method="complete",
                payload=payload,
                retry_count=MAX_WEBHOOK_RETRIES,
                last_error="timeout after 10 retries",
                dlq_at=datetime.now(UTC),
            )
            db.add(dlq)
            await db.commit()
            dlq_id = dlq.id

            # Verify it can be retrieved from the same session's transaction.
            retrieved = (await db.execute(
                select(WebhookDLQ).where(WebhookDLQ.id == dlq_id)
            )).scalar_one_or_none()
            assert retrieved is not None
            assert retrieved.provider == "click"
            assert retrieved.method == "complete"
            assert retrieved.retry_count == MAX_WEBHOOK_RETRIES
            assert retrieved.payload == payload


class TestMoveToWebhookDLQ:
    """Test moving webhooks to DLQ."""

    @pytest.mark.asyncio
    async def test_move_to_dlq(self, migrated_db) -> None:
        """A failed webhook is parked in the DLQ with its payload + error."""
        from sqlalchemy.ext.asyncio import AsyncSession

        from app.models import Tenant

        async with AsyncSession(migrated_db, expire_on_commit=False) as db:
            # tenant_id carries a real FK (ON DELETE SET NULL) → seed a tenant.
            tenant = Tenant(slug="dlq-move-t", name="DLQ Move T")
            db.add(tenant)
            await db.flush()
            payload = {"service_id": "srv123", "amount": "50000"}

            dlq_row = await move_to_dlq(
                db,
                provider="click",
                method="prepare",
                payload=payload,
                tenant_id=str(tenant.id),
                error="connection_timeout",
            )

            assert dlq_row.provider == "click"
            assert dlq_row.method == "prepare"
            assert dlq_row.last_error == "connection_timeout"
            assert dlq_row.retry_count == MAX_WEBHOOK_RETRIES
            assert dlq_row.payload == payload


class TestWebhookErrorHandling:
    """Test webhook error classification and handling."""

    def test_permanent_error_classification(self) -> None:
        """Permanent errors are not retryable."""
        err = PermanentWebhookError("bad_signature", code=-1)
        assert not err.retryable

    def test_transient_error_classification(self) -> None:
        """Transient errors are retryable."""
        err = TransientWebhookError("timeout", code=-999)
        assert err.retryable

    @pytest.mark.asyncio
    async def test_handle_permanent_webhook_error(self, migrated_db) -> None:
        """Permanent errors are refused (400) and NOT parked in the DLQ."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(migrated_db, expire_on_commit=False) as db:
            err = PermanentWebhookError("bad_signature", code=-1)
            response, status = await handle_webhook_error(
                db,
                err,
                provider="click",
                method="prepare",
                payload={"test": "data"},
                tenant_id=None,
            )

            assert status == 400
            assert response["error"] == -1
            # A permanent error must not create a DLQ entry.
            rows = (await db.execute(select(WebhookDLQ))).scalars().all()
            assert rows == []

    @pytest.mark.asyncio
    async def test_handle_transient_webhook_error(self, migrated_db) -> None:
        """Transient errors park the webhook in the DLQ and return 503."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(migrated_db, expire_on_commit=False) as db:
            err = TransientWebhookError("timeout", code=-999)
            _response, status = await handle_webhook_error(
                db,
                err,
                provider="click",
                method="complete",
                payload={"test": "data"},
                tenant_id=None,
            )

            # Should return 503 Service Unavailable so the provider retries.
            assert status == 503

            # Should have created a DLQ entry for later replay.
            dlq_rows = (await db.execute(select(WebhookDLQ))).scalars().all()
            assert len(dlq_rows) == 1
            assert dlq_rows[0].provider == "click"
            assert dlq_rows[0].method == "complete"


class TestDLQMetrics:
    """Test DLQ monitoring and metrics."""

    @pytest.mark.asyncio
    async def test_dlq_depth_zero_initially(self, migrated_db) -> None:
        """DLQ depth is 0 when empty."""
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(migrated_db, expire_on_commit=False) as db:
            depth = await get_dlq_depth(db)
            assert depth == 0

    @pytest.mark.asyncio
    async def test_dlq_depth_after_move(self, migrated_db) -> None:
        """DLQ depth increases after parking a webhook."""
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(migrated_db, expire_on_commit=False) as db:
            assert await get_dlq_depth(db) == 0

            await move_to_dlq(
                db,
                provider="click",
                method="prepare",
                payload={"test": "data"},
                tenant_id=None,
                error="test error",
            )

            assert await get_dlq_depth(db) == 1
