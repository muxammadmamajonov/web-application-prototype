"""(#196 capture) record_referral_intent: stamp referred_by, guard self/dup/unknown."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import User
from app.services import referral


async def test_record_referral_intent(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        referrer = User(telegram_id=1196001, first_name="Ref", ref_code="ABC123")
        referee = User(telegram_id=1196002, first_name="Ree")
        s.add_all([referrer, referee])
        await s.flush()

        # unknown code -> no stamp
        assert await referral.record_referral_intent(s, referee, "NOPE") is False
        assert referee.referred_by_user_id is None

        # valid code -> stamp
        assert await referral.record_referral_intent(s, referee, "ABC123") is True
        assert referee.referred_by_user_id == referrer.id

        # already attributed -> first referrer wins (no overwrite)
        assert await referral.record_referral_intent(s, referee, "ABC123") is False

        # self-referral -> never stamps
        assert await referral.record_referral_intent(s, referrer, "ABC123") is False
        assert referrer.referred_by_user_id is None

        # blank code -> no-op
        assert await referral.record_referral_intent(s, User(telegram_id=1196003), "") is False
