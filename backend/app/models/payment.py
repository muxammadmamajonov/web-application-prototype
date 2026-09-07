"""Payments (receipts) + per-tenant provider credentials."""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.currency import SUPPORTED_CURRENCIES
from app.db import Base

from .base import TimestampMixin, dt_aware, money, uuid_pk
from .enums import PaymentMethod, PaymentProvider, PaymentStatus, pg_enum

#: Same rendering as `installment._CURRENCY_TUPLE_SQL`, derived from the same
#: allowlist. Built here rather than imported to keep `payment` → `installment`
#: a type-checking-only edge.
_CURRENCY_TUPLE_SQL = "(" + ", ".join(f"'{c}'" for c in SUPPORTED_CURRENCIES) + ")"

if TYPE_CHECKING:
    from .customer import Customer
    from .installment import Installment
    from .tenant import Tenant
    from .user import User


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    installment_id: Mapped[UUID] = mapped_column(
        ForeignKey("installments.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True,
    )

    amount: Mapped[money]
    # Copied from the parent contract, never from the shop's current
    # settings — and held to that by `fk_payments_contract_currency` below. No
    # Python default, for the same reason as `Installment.currency`.
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(
        pg_enum(PaymentMethod, "payment_method"), nullable=False,
    )
    status: Mapped[PaymentStatus] = mapped_column(
        pg_enum(PaymentStatus, "payment_status"),
        default=PaymentStatus.pending, nullable=False, index=True,
    )

    receipt_image_url: Mapped[str | None] = mapped_column(String(500))
    external_ref: Mapped[str | None] = mapped_column(String(128), index=True)
    provider_payload: Mapped[dict | None] = mapped_column(JSONB)

    paid_at: Mapped[dt_aware]
    submitted_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    approved_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    approved_at: Mapped[dt_aware]
    notes: Mapped[str | None] = mapped_column(String(500))
    # (#121) Late-fee rows only: the day the fee is accrued for. Backs the partial
    # unique index so a double cron-run can't charge two fees per installment/day.
    fee_date: Mapped[date | None] = mapped_column(Date)
    # (MUDD-74) Client-supplied idempotency key for POST /tenant/payments. A
    # double-tap or network retry that carries the same key collides on
    # uq_payments_idem, so the endpoint returns the existing payment instead of
    # writing a second one — mirrors ``Installment.idempotency_key`` (F-12).
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    tenant: Mapped[Tenant] = relationship()
    installment: Mapped[Installment] = relationship(
        back_populates="payments",
        primaryjoin="Payment.installment_id == Installment.id",
        foreign_keys="Payment.installment_id",
    )
    customer: Mapped[Customer] = relationship()
    submitted_by: Mapped[User | None] = relationship(foreign_keys=[submitted_by_user_id])
    approved_by: Mapped[User | None] = relationship(foreign_keys=[approved_by_user_id])

    __table_args__ = (
        # Provider transaction ids must be unique (cash payments leave it NULL;
        # multiple NULLs are allowed). DB-level idempotency for webhook retries.
        Index(
            "uq_payments_external_ref", "external_ref",
            unique=True, postgresql_where=text("external_ref IS NOT NULL"),
        ),
        # Status-filtered ledger read: WHERE tenant_id=? AND status=? ORDER BY
        # created_at DESC. MUDD-84 made this a primary path (status filtering and
        # pagination moved server-side), so it keeps its own index.
        Index("ix_payments_tenant_status_created", "tenant_id", "status", "created_at"),
        # (MUDD-88) The UNFILTERED ledger read: WHERE tenant_id=? ORDER BY
        # created_at DESC. The composite above cannot serve it — `status` sits
        # between the two columns the query uses, so the rows arrive ordered by
        # (status, created_at) and Postgres seq-scans + sorts. Measured on a 60k
        # shape-equivalent table: 8.766 ms Seq Scan + sort -> 0.063 ms Index Scan
        # Backward. Merging the two into (tenant_id, created_at, status) was
        # measured and rejected: it regresses the status-filtered path above,
        # badly at depth (offset 4000: 1.895 ms -> 3.552 ms, index abandoned).
        Index("ix_payments_tenant_created", "tenant_id", "created_at"),
        # (audit/perf) Platform revenue aggregates filter+group on approved_at for
        # approved rows ACROSS all tenants (no tenant_id) — partial index so the
        # cross-tenant SUM/GROUP-BY doesn't seq-scan the whole payments table.
        Index(
            "ix_payments_approved_at", "approved_at",
            postgresql_where=text("status = 'approved'"),
        ),
        # (#121) At most one late-fee accrual per installment per day — a double
        # cron-run hits this and raises IntegrityError instead of double-charging.
        Index(
            "uq_payments_late_fee_day", "installment_id", "fee_date",
            unique=True, postgresql_where=text("method = 'late_fee'"),
        ),
        # (MUDD-74) At most one payment per (tenant, idempotency_key) — a
        # replayed POST /tenant/payments collides here and the endpoint returns
        # the existing payment. NULL keys (the vast majority — most payments
        # never carry one) are excluded by the partial WHERE, same as
        # uq_installments_idem.
        Index(
            "uq_payments_idem", "tenant_id", "idempotency_key",
            unique=True, postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # Unindexed FKs → user delete would seq-scan payments; "my approvals" JOIN.
        Index("ix_payments_submitted_by", "submitted_by_user_id"),
        Index("ix_payments_approved_by", "approved_by_user_id"),
        CheckConstraint("amount > 0", name="amount_pos"),
        # Only currencies the product can actually hold.
        CheckConstraint(
            "currency IN " + _CURRENCY_TUPLE_SQL, name="ck_payments_currency",
        ),
        # A payment is denominated in its contract's currency, enforced
        # by the database. An app bug — or a hand-written UPDATE — cannot record
        # a USD payment against a so'm contract, which would make `paid_amount`
        # a sum of two different units.
        ForeignKeyConstraint(
            ["installment_id", "currency"],
            ["installments.id", "installments.currency"],
            name="fk_payments_contract_currency", ondelete="CASCADE",
        ),
    )


class TenantPaymentProvider(Base, TimestampMixin):
    """
    Per-tenant credentials for an online payment provider.
    Each tenant can configure their own Click/Payme merchant; platform-level
    fallbacks in env vars are used only for Muddato's own SaaS billing.
    """

    __tablename__ = "tenant_payment_providers"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    provider: Mapped[PaymentProvider] = mapped_column(
        pg_enum(PaymentProvider, "payment_provider"), nullable=False,
    )
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    service_id: Mapped[str | None] = mapped_column(String(64))   # Click only
    # Stored encrypted at rest in production (TODO: KMS). Schema-level it's just text.
    secret_key_ciphertext: Mapped[str] = mapped_column(String(1024), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_tenant_provider"),
    )


class ReceiptBlob(Base, TimestampMixin):
    """Binary store for customer-uploaded cash-payment receipt images.

    Kept in its own table (not on ``payments``) so payment lists/queries stay
    light — the row only holds a short capability URL. The image is served
    lazily by an HMAC-signed endpoint, so it loads only when actually rendered.
    """

    __tablename__ = "receipt_blobs"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True,
    )
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # (#96) Exactly one of these holds the bytes: ``object_key`` (receipt lives in
    # a private object-storage bucket) or ``data`` (legacy Postgres blob). ``data``
    # is nullable so object-stored rows don't carry the image in the primary DB.
    object_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class KycBlob(Base, TimestampMixin):
    """Private store for customer/guarantor KYC images — passport / national-ID /
    employment-record scans (#113).

    Mirrors ``ReceiptBlob``, but a KYC blob is reachable ONLY through the
    tenant-authenticated ``/tenant/kyc-media`` proxy — never via an HMAC
    capability URL — so identity photos are never world-readable (the prior
    archive.org path left the bytes public). The bytes live in a private
    object-storage bucket (``object_key``) or, by default, in Postgres (``data``);
    exactly one is set.
    """

    __tablename__ = "kyc_blobs"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(256))
    data: Mapped[bytes | None] = mapped_column(LargeBinary)
