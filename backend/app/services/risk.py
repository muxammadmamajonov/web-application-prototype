"""Cross-tenant risk scoring + signal hashing.

Privacy contract (Round 23):
  * JSHSHIR plaintext NEVER crosses the tenant boundary — only a salted
    SHA-256 hash is stored and matched.
  * A consuming tenant sees only ``signal_count`` + ``severity`` of external
    signals — never the source tenant.
  * External signals are read ONLY for tenants that have opted in (the
    network is reciprocal).
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Customer,
    CustomerRiskSignal,
    Installment,
    Payment,
    TenantSettings,
)
from app.models.enums import (
    InstallmentStatus,
    PaymentStatus,
    RiskSeverity,
    RiskSignalSource,
    RiskSignalStatus,
)

# Ascending order → used to pick the "worst" severity and to weight scores.
_SEVERITY_ORDER: list[RiskSeverity] = [
    RiskSeverity.late_30, RiskSeverity.late_60, RiskSeverity.late_90,
    RiskSeverity.charged_off, RiskSeverity.fraud_reported,
]
_SEVERITY_RANK = {s: i for i, s in enumerate(_SEVERITY_ORDER)}

APPEAL_WINDOW_DAYS = 14
# An installment this many days overdue auto-generates a network signal.
AUTO_PUBLISH_OVERDUE_DAYS = 60


def normalize_jshshir(jshshir: str | None) -> str | None:
    """Strip to the 14 significant digits; return None if not 14 digits."""
    if not jshshir:
        return None
    digits = "".join(c for c in jshshir if c.isdigit())
    return digits if len(digits) == 14 else None


def hash_jshshir(jshshir: str | None) -> str | None:
    """HMAC-SHA256 of a normalized JSHSHIR, keyed by RISK_HASH_SALT. None if the
    value is not a valid 14-digit id.

    HMAC (not a bare ``sha256(salt:msg)``) is the correct keyed-hash construction,
    and the key is a required production secret — config refuses to boot on the
    public dev default (#72) — so the 14-digit identifier space cannot be
    rainbow-tabled by anyone who doesn't hold the key.
    """
    norm = normalize_jshshir(jshshir)
    if norm is None:
        return None
    return hmac.new(
        settings.risk_hash_salt.encode(), norm.encode(), hashlib.sha256,
    ).hexdigest()


def severity_for_days_overdue(days: int) -> RiskSeverity | None:
    if days >= 90:
        return RiskSeverity.late_90
    if days >= 60:
        return RiskSeverity.late_60
    if days >= 30:
        return RiskSeverity.late_30
    return None


# ── internal (own-tenant) history ───────────────────────────────────────────

async def _internal_history(db: AsyncSession, customer: Customer) -> dict[str, Any]:
    """Payment behaviour from THIS tenant's own data for the customer."""
    insts = (await db.execute(
        select(Installment).where(Installment.customer_id == customer.id)
    )).scalars().all()
    total = len(insts)
    active_overdue = sum(1 for i in insts if i.status == InstallmentStatus.overdue)
    completed = sum(1 for i in insts if i.status == InstallmentStatus.completed)

    # On-time rate: approved payments vs. total approved payments. We treat a
    # late_fee payment as evidence of at least one late month.
    approved = (await db.execute(
        select(func.count(Payment.id)).where(
            Payment.customer_id == customer.id,
            Payment.status == PaymentStatus.approved,
        )
    )).scalar_one()
    late_fee_count = (await db.execute(
        select(func.count(Payment.id)).where(
            Payment.customer_id == customer.id,
            Payment.notes.like("[late_fee]%"),
        )
    )).scalar_one()

    on_time_rate = None
    if approved:
        on_time_rate = round(max(0, approved - late_fee_count) / approved, 3)

    return {
        "installments_total": total,
        "completed": completed,
        "active_overdue": active_overdue,
        "approved_payments": approved,
        "late_fee_count": late_fee_count,
        "on_time_rate": on_time_rate,
    }


# ── external (cross-tenant) signals ──────────────────────────────────────────

async def _external_signals(
    db: AsyncSession, *, jshshir_hash: str, exclude_tenant_id: UUID,
) -> dict[str, Any]:
    """Aggregate PUBLISHED signals from OTHER tenants on this hash.

    Returns only count + severity breakdown — never source tenant ids.
    """
    now = datetime.now(UTC)
    rows = (await db.execute(
        select(CustomerRiskSignal.severity, func.count(CustomerRiskSignal.id))
        .where(
            CustomerRiskSignal.jshshir_hash == jshshir_hash,
            CustomerRiskSignal.status == RiskSignalStatus.published,
            CustomerRiskSignal.source_tenant_id != exclude_tenant_id,
            (CustomerRiskSignal.expires_at.is_(None))
            | (CustomerRiskSignal.expires_at > now),
        )
        .group_by(CustomerRiskSignal.severity)
    )).all()
    by_severity = {sev.value: cnt for sev, cnt in rows}
    total = sum(by_severity.values())
    worst = None
    for sev in reversed(_SEVERITY_ORDER):
        if by_severity.get(sev.value):
            worst = sev.value
            break
    # Approximate number of distinct shops (count, not identities).
    distinct_shops = (await db.execute(
        select(func.count(func.distinct(CustomerRiskSignal.source_tenant_id)))
        .where(
            CustomerRiskSignal.jshshir_hash == jshshir_hash,
            CustomerRiskSignal.status == RiskSignalStatus.published,
            CustomerRiskSignal.source_tenant_id != exclude_tenant_id,
        )
    )).scalar_one()
    return {
        "signal_count": total,
        "by_severity": by_severity,
        "worst_severity": worst,
        "distinct_shops": distinct_shops,
    }


def _tier(internal: dict[str, Any], external: dict[str, Any]) -> str:
    """Combine internal + external into low | medium | high."""
    ext_count = external.get("signal_count", 0)
    worst = external.get("worst_severity")
    # Any fraud report or charge-off anywhere → high.
    if worst in (RiskSeverity.fraud_reported.value, RiskSeverity.charged_off.value):
        return "high"
    if worst == RiskSeverity.late_90.value or ext_count >= 3:
        return "high"
    if ext_count >= 1:
        return "medium"
    # Internal-only signals.
    if internal.get("active_overdue", 0) >= 1 or (internal.get("late_fee_count", 0) >= 2):
        return "medium"
    return "low"


async def score(db: AsyncSession, *, tenant_id: UUID, customer: Customer) -> dict[str, Any]:
    """Risk profile for a customer.

    External signals are included only if THIS tenant has opted in. Returns
    a privacy-safe dict (no source tenants, no plaintext PII).
    """
    internal = await _internal_history(db, customer)

    opted_in = (await db.execute(
        select(TenantSettings.risk_network_opt_in).where(
            TenantSettings.tenant_id == tenant_id
        )
    )).scalar_one_or_none() or False

    jhash = hash_jshshir(customer.jshshir)
    external: dict[str, Any] = {
        "signal_count": 0, "by_severity": {}, "worst_severity": None,
        "distinct_shops": 0,
    }
    if opted_in and jhash:
        external = await _external_signals(
            db, jshshir_hash=jhash, exclude_tenant_id=tenant_id,
        )

    return {
        "tier": _tier(internal, external),
        "has_jshshir": jhash is not None,
        "network_opted_in": bool(opted_in),
        "internal": internal,
        "external": external,
    }


# ── writing signals ──────────────────────────────────────────────────────────

async def upsert_signal(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    customer: Customer,
    severity: RiskSeverity,
    source: RiskSignalSource,
    status: RiskSignalStatus,
    installment_id: UUID | None = None,
    appeal_deadline: date | None = None,
    evidence: dict[str, Any] | None = None,
) -> CustomerRiskSignal | None:
    """Create a signal if no active (non-withdrawn) one of the same
    (customer, severity) already exists. Returns the new/None row.

    No-op (returns None) when the customer has no valid JSHSHIR — without it
    there's nothing to match cross-tenant.
    """
    jhash = hash_jshshir(customer.jshshir)
    if jhash is None:
        return None

    existing = (await db.execute(
        select(CustomerRiskSignal).where(
            CustomerRiskSignal.source_tenant_id == tenant_id,
            CustomerRiskSignal.source_customer_id == customer.id,
            CustomerRiskSignal.severity == severity,
            CustomerRiskSignal.status != RiskSignalStatus.withdrawn,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return None

    sig = CustomerRiskSignal(
        source_tenant_id=tenant_id,
        source_customer_id=customer.id,
        source_installment_id=installment_id,
        jshshir_hash=jhash,
        severity=severity,
        source=source,
        status=status,
        appeal_deadline=appeal_deadline,
        evidence_json=evidence or {},
    )
    db.add(sig)
    await db.flush()
    return sig


__all__ = (
    "APPEAL_WINDOW_DAYS",
    "AUTO_PUBLISH_OVERDUE_DAYS",
    "hash_jshshir",
    "normalize_jshshir",
    "score",
    "severity_for_days_overdue",
    "upsert_signal",
)
