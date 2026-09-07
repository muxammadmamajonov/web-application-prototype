"""Webhook authentication and idempotency tests.

Verify that:
1. Duplicate external_ref is idempotent (database constraint)
2. Late-fee deduplication via unique (installment_id, fee_date) index
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    Customer,
    Installment,
    Payment,
    Tenant,
)
from app.models.enums import (
    InstallmentStatus,
    PaymentMethod,
    PaymentStatus,
    TenantStatus,
)

NOW = datetime.now(UTC)
TODAY = NOW.date()


# ──────────────────────────────────────────────────────────────────────────────
# IDEMPOTENCY: Duplicate external_ref handling
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_external_ref_raises_integrity_error(migrated_db):
    """Two payments with the same external_ref raise IntegrityError."""
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as db:
        # Setup
        tenant = Tenant(
            id=uuid4(),
            slug=f"webhook-{uuid4().hex[:8]}",
            name="Webhook Tenant",
            status=TenantStatus.active,
            balance=Decimal("0"),
        )
        db.add(tenant)
        await db.flush()

        customer = Customer(
            id=uuid4(),
            tenant_id=tenant.id,
            phone=f"998{uuid4().hex[:10]}",
            full_name="Webhook Customer",
            status="active",
        )
        db.add(customer)
        await db.flush()

        inst = Installment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            customer_id=customer.id,
            total_amount=Decimal("100.00"),
            principal=Decimal("100.00"),
            months=10,
            monthly_amount=Decimal("10.00"),
            status=InstallmentStatus.active,
            start_date=TODAY,
            next_due_date=TODAY + timedelta(days=30),
            calc_formula="none",
        )
        db.add(inst)
        await db.flush()

        # First payment with external_ref
        pmt1 = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("30.00"),
            method=PaymentMethod.payme,
            status=PaymentStatus.approved,
            external_ref="payme-tx-001",
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(pmt1)
        await db.flush()

        # Duplicate external_ref should raise
        pmt2 = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("30.00"),
            method=PaymentMethod.payme,
            status=PaymentStatus.approved,
            external_ref="payme-tx-001",  # Same!
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(pmt2)

        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_null_external_ref_allowed(migrated_db):
    """Multiple payments with NULL external_ref are allowed (cash payments)."""
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as db:
        # Setup
        tenant = Tenant(
            id=uuid4(),
            slug=f"webhook-{uuid4().hex[:8]}",
            name="Webhook Tenant",
            status=TenantStatus.active,
            balance=Decimal("0"),
        )
        db.add(tenant)
        await db.flush()

        customer = Customer(
            id=uuid4(),
            tenant_id=tenant.id,
            phone=f"998{uuid4().hex[:10]}",
            full_name="Webhook Customer",
            status="active",
        )
        db.add(customer)
        await db.flush()

        inst = Installment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            customer_id=customer.id,
            total_amount=Decimal("100.00"),
            principal=Decimal("100.00"),
            months=10,
            monthly_amount=Decimal("10.00"),
            status=InstallmentStatus.active,
            start_date=TODAY,
            next_due_date=TODAY + timedelta(days=30),
            calc_formula="none",
        )
        db.add(inst)
        await db.flush()

        # Multiple cash payments (NULL external_ref) are OK
        pmt1 = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("30.00"),
            method=PaymentMethod.cash,
            status=PaymentStatus.approved,
            external_ref=None,
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(pmt1)
        await db.flush()

        pmt2 = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("20.00"),
            method=PaymentMethod.cash,
            status=PaymentStatus.approved,
            external_ref=None,
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(pmt2)
        await db.flush()

        # Both should exist
        from sqlalchemy import func, select
        count = await db.scalar(
            select(func.count(Payment.id)).where(
                Payment.installment_id == inst.id,
                Payment.method == PaymentMethod.cash,
            )
        )
        assert count == 2


# ──────────────────────────────────────────────────────────────────────────────
# LATE-FEE DEDUPLICATION: (installment_id, fee_date) uniqueness
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_late_fee_date_raises_integrity_error(migrated_db):
    """Two late-fee payments on same day for same installment raise IntegrityError."""
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as db:
        # Setup
        tenant = Tenant(
            id=uuid4(),
            slug=f"webhook-{uuid4().hex[:8]}",
            name="Webhook Tenant",
            status=TenantStatus.active,
            balance=Decimal("0"),
        )
        db.add(tenant)
        await db.flush()

        customer = Customer(
            id=uuid4(),
            tenant_id=tenant.id,
            phone=f"998{uuid4().hex[:10]}",
            full_name="Webhook Customer",
            status="active",
        )
        db.add(customer)
        await db.flush()

        inst = Installment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            customer_id=customer.id,
            total_amount=Decimal("100.00"),
            principal=Decimal("100.00"),
            months=10,
            monthly_amount=Decimal("10.00"),
            status=InstallmentStatus.active,
            start_date=TODAY,
            next_due_date=TODAY + timedelta(days=30),
            calc_formula="none",
        )
        db.add(inst)
        await db.flush()

        fee_date = TODAY

        # First late-fee
        fee1 = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("5.00"),
            method=PaymentMethod.late_fee,
            status=PaymentStatus.approved,
            fee_date=fee_date,
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(fee1)
        await db.flush()

        # Duplicate late-fee on same day
        fee2 = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("5.00"),
            method=PaymentMethod.late_fee,
            status=PaymentStatus.approved,
            fee_date=fee_date,  # Same date
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(fee2)

        with pytest.raises(IntegrityError):
            await db.flush()


@pytest.mark.asyncio
async def test_different_late_fee_dates_allowed(migrated_db):
    """Two late-fee payments on different days for same installment are allowed."""
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as db:
        # Setup
        tenant = Tenant(
            id=uuid4(),
            slug=f"webhook-{uuid4().hex[:8]}",
            name="Webhook Tenant",
            status=TenantStatus.active,
            balance=Decimal("0"),
        )
        db.add(tenant)
        await db.flush()

        customer = Customer(
            id=uuid4(),
            tenant_id=tenant.id,
            phone=f"998{uuid4().hex[:10]}",
            full_name="Webhook Customer",
            status="active",
        )
        db.add(customer)
        await db.flush()

        inst = Installment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            customer_id=customer.id,
            total_amount=Decimal("100.00"),
            principal=Decimal("100.00"),
            months=10,
            monthly_amount=Decimal("10.00"),
            status=InstallmentStatus.active,
            start_date=TODAY,
            next_due_date=TODAY + timedelta(days=30),
            calc_formula="none",
        )
        db.add(inst)
        await db.flush()

        # First late-fee
        fee1 = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("5.00"),
            method=PaymentMethod.late_fee,
            status=PaymentStatus.approved,
            fee_date=TODAY,
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(fee1)
        await db.flush()

        # Second late-fee on different day
        fee2 = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("5.00"),
            method=PaymentMethod.late_fee,
            status=PaymentStatus.approved,
            fee_date=TODAY + timedelta(days=1),  # Different date
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(fee2)
        await db.flush()

        # Both should exist
        from sqlalchemy import func, select
        count = await db.scalar(
            select(func.count(Payment.id)).where(
                Payment.installment_id == inst.id,
                Payment.method == PaymentMethod.late_fee,
            )
        )
        assert count == 2
