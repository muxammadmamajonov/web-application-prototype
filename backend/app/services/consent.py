"""Consent capture (#168) — record + check terms / national-ID-processing consent.

The binding consent TEXT + lawful-basis wording live in ``docs/CONSENT.md``
([LEGAL_REVIEW]); this module is the recording machinery. Append-only — withdrawing
consent is a new row, never an update, so the log is a tamper-evident accountability
trail.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConsentRecord

# Bump when the terms / consent text materially changes (triggers re-consent).
TERMS_VERSION = "2026-06-08"


async def record_consent(
    db: AsyncSession,
    *,
    subject_kind: str,
    subject_id: UUID,
    kind: str,
    lawful_basis: str,
    tenant_id: UUID | None = None,
    source: str | None = None,
    version: str = TERMS_VERSION,
) -> ConsentRecord:
    """Append an immutable consent record. The caller commits."""
    rec = ConsentRecord(
        tenant_id=tenant_id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        kind=kind,
        terms_version=version,
        lawful_basis=lawful_basis,
        source=source,
        accepted_at=datetime.now(UTC),
    )
    db.add(rec)
    return rec


async def has_consent(db: AsyncSession, *, subject_kind: str, subject_id: UUID, kind: str) -> bool:
    """True if the subject has at least one record for this consent kind."""
    return (await db.execute(
        select(ConsentRecord.id).where(
            ConsentRecord.subject_kind == subject_kind,
            ConsentRecord.subject_id == subject_id,
            ConsentRecord.kind == kind,
        ).limit(1)
    )).first() is not None
