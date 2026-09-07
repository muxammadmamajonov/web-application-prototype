"""(#168) Consent capture: record + check, append-only."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import ConsentRecord
from app.services import consent


async def test_record_and_has_consent(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        sid = uuid4()
        # not consented yet
        assert await consent.has_consent(s, subject_kind="customer", subject_id=sid, kind="kyc_processing") is False

        rec = await consent.record_consent(
            s, subject_kind="customer", subject_id=sid, kind="kyc_processing",
            lawful_basis="consent", source="kyc",
        )
        await s.commit()
        assert rec.terms_version == consent.TERMS_VERSION

        # now consented for that kind, but not another kind
        assert await consent.has_consent(s, subject_kind="customer", subject_id=sid, kind="kyc_processing") is True
        assert await consent.has_consent(s, subject_kind="customer", subject_id=sid, kind="terms") is False

        # append-only: a second acceptance adds a row, never updates
        await consent.record_consent(
            s, subject_kind="customer", subject_id=sid, kind="kyc_processing", lawful_basis="consent",
        )
        await s.commit()
        n = (await s.execute(
            select(func.count(ConsentRecord.id)).where(ConsentRecord.subject_id == sid)
        )).scalar_one()
        assert n == 2
