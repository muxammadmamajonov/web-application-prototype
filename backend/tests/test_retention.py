"""#166 — data-retention purge deletes past-retention PII, keeps recent.

The Privacy Policy promises deletion; logs/notifications/expired tokens/aged
risk-signals must not accumulate forever. ``_purge_expired`` (the cron body,
parameterized by session for testing) batch-deletes rows older than their
retention window. Financial records (installments/payments) are NEVER touched.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)


async def test_purge_expired_deletes_old_audit_keeps_recent(migrated_db) -> None:
    from app.models import AuditLog
    from app.services.scheduler import _purge_expired

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        s.add(AuditLog(action="ancient", created_at=NOW - timedelta(days=400)))
        s.add(AuditLog(action="recent", created_at=NOW - timedelta(days=2)))
        await s.commit()

    async with Session() as s:
        counts = await _purge_expired(s, now=NOW)

    async with Session() as s:
        rows = (await s.execute(select(AuditLog))).scalars().all()
    assert [r.action for r in rows] == ["recent"]   # only the recent one survives
    assert counts["audit_log"] == 1                 # exactly one purged


async def test_purge_expired_deletes_expired_risk_signals(migrated_db) -> None:
    from app.models import CustomerRiskSignal
    from app.models.enums import RiskSeverity
    from app.services.scheduler import _purge_expired

    sev = RiskSeverity.late_30
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        # expired (past expires_at) → purged; still-valid + no-expiry → kept
        s.add(CustomerRiskSignal(jshshir_hash="h1", severity=sev, expires_at=NOW - timedelta(days=1)))
        s.add(CustomerRiskSignal(jshshir_hash="h2", severity=sev, expires_at=NOW + timedelta(days=30)))
        s.add(CustomerRiskSignal(jshshir_hash="h3", severity=sev, expires_at=None))
        await s.commit()

    async with Session() as s:
        counts = await _purge_expired(s, now=NOW)

    async with Session() as s:
        hashes = sorted(
            (await s.execute(select(CustomerRiskSignal.jshshir_hash))).scalars().all()
        )
    assert hashes == ["h2", "h3"]                   # expired h1 gone; valid + no-expiry kept
    assert counts["risk_signals"] == 1
