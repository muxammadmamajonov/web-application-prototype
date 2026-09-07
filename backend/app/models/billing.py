"""Subscription invoices — the billing ledger (Round 27).  # noqa: RUF002

One ``Invoice`` row per tenant per billing period. Fixed plans (Lite/Pro)
bill a flat monthly price; the Custom plan bills
``active_installments_count × unit_price``. Invoices drive dunning: unpaid  # noqa: RUF002
past ``due_at`` → ``overdue`` → tenant suspend (+3d) → block (+14d).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

from .base import TimestampMixin, money, uuid_pk
from .enums import InvoiceStatus, pg_enum

if TYPE_CHECKING:
    from .tenant import Tenant


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoices"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # Plan snapshot — the invoice records what plan was billed even if the
    # tenant later switches.
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False)
    # (#99) Gapless sequential legal invoice number (e.g. "2026-000001"). Unique;
    # assigned from a transaction-safe counter so rollbacks leave no gaps.
    invoice_number: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Custom-plan usage snapshot (NULL for fixed plans).
    active_installments_count: Mapped[int | None] = mapped_column(Integer)
    unit_price: Mapped[money | None]

    amount: Mapped[money]
    status: Mapped[InvoiceStatus] = mapped_column(
        pg_enum(InvoiceStatus, "invoice_status"),
        default=InvoiceStatus.open, nullable=False, index=True,
    )
    # Optional line-item breakdown for the PDF / UI.
    line_items: Mapped[dict | list | None] = mapped_column(JSONB)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    payment_method: Mapped[str | None] = mapped_column(String(20))   # click | payme | manual
    provider_ref: Mapped[str | None] = mapped_column(String(120))

    tenant: Mapped[Tenant] = relationship()

    __table_args__ = (
        # One invoice per tenant per billing period — prevents double-billing
        # if the billing cron is retried or fires twice.
        UniqueConstraint("tenant_id", "period_start", name="uq_invoice_tenant_period"),
    )


class SequenceCounter(Base):
    """A gapless, transaction-safe named counter (#99).

    Legal invoice numbers must be gapless. A plain DB SEQUENCE leaves gaps (a
    rolled-back txn consumes a value); this counter is bumped by an atomic
    ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` inside the invoice
    transaction, so concurrent issuers serialize on the row and a rollback rolls
    the increment back too — no gaps.
    """

    __tablename__ = "sequence_counters"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WebhookDLQ(Base, TimestampMixin):
    """Dead-letter queue for failed webhooks (#754).

    When a webhook fails after MAX_WEBHOOK_RETRIES attempts, it's moved here
    for manual inspection and replay. Tracks:
    - The original webhook payload (provider, method, form data)
    - Retry count and error details
    - Tenant context for audit/replay

    Monitoring: alert when dlq_depth > 0; manual replay via admin endpoint.
    """

    __tablename__ = "webhook_dlq"

    id: Mapped[uuid_pk]
    tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False)  # "prepare", "complete", etc.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(1024))
    dlq_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
