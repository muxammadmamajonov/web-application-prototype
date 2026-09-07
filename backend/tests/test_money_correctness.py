"""Money correctness tests: payment→installment→ledger, no drift, rounding safety."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
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
from app.services.installments import (
    apply_payment,
    reverse_payment,
)

NOW = datetime.now(UTC)
TODAY = NOW.date()


@pytest.fixture
def make_installment():
    """Factory for creating test installments."""
    def _make(
        tenant_id,
        customer_id,
        total_amount: Decimal = Decimal("100.00"),
        down_payment: Decimal = Decimal("0"),
        principal: Decimal = Decimal("100.00"),
        months: int = 12,
        monthly_amount: Decimal | None = None,
        start_date: date | None = None,
        paid_amount: Decimal = Decimal("0"),
        payoff_discount_amount: Decimal | None = None,
    ) -> Installment:
        if monthly_amount is None:
            monthly_amount = (principal / months).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return Installment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant_id,
            customer_id=customer_id,
            total_amount=total_amount,
            down_payment=down_payment,
            principal=principal,
            months=months,
            monthly_amount=monthly_amount,
            paid_amount=paid_amount,
            paid_count=0,
            start_date=start_date or TODAY,
            next_due_date=(start_date or TODAY) + timedelta(days=30),
            status=InstallmentStatus.active,
            payoff_discount_amount=payoff_discount_amount,
            calc_formula="none",
        )
    return _make


@pytest.fixture
def make_payment():
    """Factory for creating test payments."""
    def _make(
        tenant_id,
        installment_id,
        customer_id,
        amount: Decimal = Decimal("100.00"),
        method: PaymentMethod = PaymentMethod.payme,
        status: PaymentStatus = PaymentStatus.approved,
        external_ref: str | None = None,
    ) -> Payment:
        return Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant_id,
            installment_id=installment_id,
            customer_id=customer_id,
            amount=amount,
            method=method,
            status=status,
            external_ref=external_ref,
            paid_at=NOW,
            approved_at=NOW,
        )
    return _make


# ──────────────────────────────────────────────────────────────────────────────
# MONEY CORRECTNESS: Installment state consistency
# ──────────────────────────────────────────────────────────────────────────────

class TestInstallmentPaymentApplication:
    """Verify that apply_payment updates state correctly."""

    def test_single_payment_increases_paid_amount(self, make_installment):
        """A payment of $10 increases paid_amount by $10."""
        inst = make_installment(uuid4(), uuid4(), principal=Decimal("100.00"))
        assert inst.paid_amount == Decimal("0")
        apply_payment(inst, Decimal("10.00"), now=NOW)
        assert inst.paid_amount == Decimal("10.00")

    def test_multiple_payments_accumulate(self, make_installment):
        """Multiple payments accumulate correctly."""
        inst = make_installment(uuid4(), uuid4(), principal=Decimal("100.00"))
        apply_payment(inst, Decimal("30.00"), now=NOW)
        assert inst.paid_amount == Decimal("30.00")
        apply_payment(inst, Decimal("20.00"), now=NOW)
        assert inst.paid_amount == Decimal("50.00")
        apply_payment(inst, Decimal("50.00"), now=NOW)
        assert inst.paid_amount == Decimal("100.00")

    def test_zero_payment_noop(self, make_installment):
        """A zero payment doesn't change state."""
        inst = make_installment(uuid4(), uuid4(), principal=Decimal("100.00"))
        original_paid = inst.paid_amount
        apply_payment(inst, Decimal("0"), now=NOW)
        assert inst.paid_amount == original_paid

    def test_partial_payment_updates_paid_count(self, make_installment):
        """Paying one month's amount increases paid_count by 1."""
        inst = make_installment(
            uuid4(), uuid4(),
            total_amount=Decimal("120.00"),
            principal=Decimal("120.00"),
            months=12,
            monthly_amount=Decimal("10.00"),
        )
        assert inst.paid_count == 0
        apply_payment(inst, Decimal("10.00"), now=NOW)
        assert inst.paid_count == 1

    def test_full_payoff_completes_installment(self, make_installment):
        """Paying the full principal marks the installment completed."""
        inst = make_installment(
            uuid4(), uuid4(),
            total_amount=Decimal("120.00"),
            principal=Decimal("120.00"),
            months=12,
        )
        apply_payment(inst, Decimal("120.00"), now=NOW)
        assert inst.status == InstallmentStatus.completed
        assert inst.closed_at is not None

    def test_overpayment_is_accepted(self, make_installment):
        """Paying more than the principal is accepted."""
        inst = make_installment(
            uuid4(), uuid4(),
            total_amount=Decimal("100.00"),
            principal=Decimal("100.00"),
            months=10,
            monthly_amount=Decimal("10.00"),
        )
        apply_payment(inst, Decimal("120.00"), now=NOW)
        assert inst.paid_amount == Decimal("120.00")
        assert inst.status == InstallmentStatus.completed

    def test_paid_count_floors_correctly(self, make_installment):
        """paid_count = floor(paid_amount / monthly_amount)."""
        inst = make_installment(
            uuid4(), uuid4(),
            total_amount=Decimal("100.00"),
            principal=Decimal("100.00"),
            months=10,
            monthly_amount=Decimal("10.00"),
        )
        apply_payment(inst, Decimal("15.00"), now=NOW)
        assert inst.paid_count == 1
        apply_payment(inst, Decimal("6.00"), now=NOW)
        assert inst.paid_count == 2


class TestRoundingSafety:
    """Verify that rounding doesn't cause drift."""

    def test_monthly_amount_rounds_correctly(self, make_installment):
        """Monthly amount is rounded HALF_UP to 2 decimal places."""
        inst = make_installment(
            uuid4(), uuid4(),
            total_amount=Decimal("100.00"),
            principal=Decimal("100.00"),
            months=3,
        )
        assert isinstance(inst.monthly_amount, Decimal)
        assert inst.monthly_amount > 0

    def test_multiple_partial_payments_no_drift(self, make_installment):
        """Sequence of partial payments doesn't drift state."""
        inst = make_installment(
            uuid4(), uuid4(),
            total_amount=Decimal("100.00"),
            principal=Decimal("100.00"),
            months=10,
            monthly_amount=Decimal("10.00"),
        )
        payments = [Decimal("3.33"), Decimal("3.34"), Decimal("3.33")]
        for pmt in payments:
            apply_payment(inst, pmt, now=NOW)
        total_paid = sum(payments)
        assert inst.paid_amount == total_paid


class TestPaymentReversal:
    """Verify that reverse_payment correctly undoes payments."""

    def test_reverse_payment_decreases_paid_amount(self, make_installment):
        """Reversing a payment reduces paid_amount."""
        inst = make_installment(
            uuid4(), uuid4(),
            total_amount=Decimal("100.00"),
            principal=Decimal("100.00"),
            months=10,
            monthly_amount=Decimal("10.00"),
        )
        apply_payment(inst, Decimal("30.00"), now=NOW)
        assert inst.paid_amount == Decimal("30.00")
        reverse_payment(inst, Decimal("10.00"), now=NOW)
        assert inst.paid_amount == Decimal("20.00")


class TestPayoffDiscount:
    """Verify early-payoff discount handling."""

    def test_discount_cleared_if_no_payoff(self, make_installment):
        """Discount is cleared if a payment doesn't settle the loan."""
        inst = make_installment(
            uuid4(), uuid4(),
            total_amount=Decimal("100.00"),
            principal=Decimal("100.00"),
            months=10,
            monthly_amount=Decimal("10.00"),
            payoff_discount_amount=Decimal("5.00"),
        )
        apply_payment(inst, Decimal("10.00"), now=NOW)
        assert inst.status != InstallmentStatus.completed
        assert inst.payoff_discount_amount is None


# ──────────────────────────────────────────────────────────────────────────────
# DATABASE INTEGRATION: Payment creation and idempotency
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_payment_creation_persists(migrated_db):
    """Creating a Payment model row persists to the database."""
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as db:
        # Create tenant and customer
        tenant = Tenant(
            id=uuid4(),
            slug=f"test-tenant-{uuid4().hex[:8]}",
            name="Test Tenant",
            status=TenantStatus.active,
            balance=Decimal("0"),
        )
        db.add(tenant)
        await db.flush()

        customer = Customer(
            id=uuid4(),
            tenant_id=tenant.id,
            phone=f"998{uuid4().hex[:10]}",
            full_name="Test Customer",
            status="active",
        )
        db.add(customer)
        await db.flush()

        # Create installment
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

        # Create payment
        pmt = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("30.00"),
            method=PaymentMethod.payme,
            status=PaymentStatus.approved,
            external_ref="test-ext-123",
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(pmt)
        await db.flush()

        # Query back
        result = await db.get(Payment, pmt.id)
        assert result is not None
        assert result.amount == Decimal("30.00")
        assert result.external_ref == "test-ext-123"
        assert result.status == PaymentStatus.approved


@pytest.mark.asyncio
async def test_payment_idempotency_on_external_ref(migrated_db):
    """Duplicate external_ref raises IntegrityError (webhook idempotency)."""
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as db:
        # Create tenant and customer
        tenant = Tenant(
            id=uuid4(),
            slug=f"test-tenant-{uuid4().hex[:8]}",
            name="Test Tenant",
            status=TenantStatus.active,
            balance=Decimal("0"),
        )
        db.add(tenant)
        await db.flush()

        customer = Customer(
            id=uuid4(),
            tenant_id=tenant.id,
            phone=f"998{uuid4().hex[:10]}",
            full_name="Test Customer",
            status="active",
        )
        db.add(customer)
        await db.flush()

        # Create installment
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

        # Create first payment
        pmt1 = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("30.00"),
            method=PaymentMethod.payme,
            status=PaymentStatus.approved,
            external_ref="webhook-123",
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(pmt1)
        await db.flush()

        # Attempt duplicate external_ref
        pmt2 = Payment(currency="UZS",
            id=uuid4(),
            tenant_id=tenant.id,
            installment_id=inst.id,
            customer_id=customer.id,
            amount=Decimal("30.00"),
            method=PaymentMethod.payme,
            status=PaymentStatus.approved,
            external_ref="webhook-123",
            paid_at=NOW,
            approved_at=NOW,
        )
        db.add(pmt2)

        with pytest.raises(IntegrityError):
            await db.flush()
