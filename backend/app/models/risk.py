"""Cross-tenant risk signals (opt-in network).

A risk signal is the *only* thing that crosses the tenant boundary, and it
never carries plaintext PII: customers are keyed by ``jshshir_hash`` (a
salted SHA-256 of the 14-digit national identifier). A consuming tenant only
ever learns the *count* and *severity* of signals on a hash — never which
tenant raised them. See ``app/services/risk.py``.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

from .base import TimestampMixin, dt_aware, uuid_pk
from .enums import RiskSeverity, RiskSignalSource, RiskSignalStatus, pg_enum

if TYPE_CHECKING:
    from .tenant import Tenant


class CustomerRiskSignal(Base, TimestampMixin):
    __tablename__ = "customer_risk_signals"

    id: Mapped[uuid_pk]
    # The tenant that raised this signal. Kept for audit + withdrawal, never
    # surfaced to consuming tenants. SET NULL so the signal survives a tenant
    # deletion (the network memory outlives any single shop).
    source_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), index=True,
    )
    # Source tenant's own customer row that triggered this (nullable; lets us
    # withdraw the signal if that installment later gets paid off).
    source_customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"),
    )
    source_installment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("installments.id", ondelete="SET NULL"),
    )

    # Salted SHA-256 of the customer's JSHSHIR — the cross-tenant join key.
    jshshir_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    severity: Mapped[RiskSeverity] = mapped_column(
        pg_enum(RiskSeverity, "risk_severity"), nullable=False, index=True,
    )
    source: Mapped[RiskSignalSource] = mapped_column(
        pg_enum(RiskSignalSource, "risk_signal_source"),
        default=RiskSignalSource.auto, nullable=False,
    )
    status: Mapped[RiskSignalStatus] = mapped_column(
        pg_enum(RiskSignalStatus, "risk_signal_status"),
        default=RiskSignalStatus.pending_appeal, nullable=False, index=True,
    )

    # End of the 14-day appeal window for auto signals; once past, the daily
    # job promotes ``pending_appeal`` → ``published``.
    appeal_deadline: Mapped[date | None] = mapped_column(Date)
    # Optional natural expiry (e.g. signals age out after N months).
    expires_at: Mapped[dt_aware]

    # Non-PII evidence: {"days_overdue": 63, "remaining": "2550000", ...}.
    evidence_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    source_tenant: Mapped[Tenant | None] = relationship()
