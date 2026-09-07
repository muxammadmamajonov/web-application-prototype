"""(#196 conversion) maybe_award_referral_conversion: gated, idempotent, ledger credit."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import PlatformSetting, Referral, Tenant, TenantBalanceTxn, TenantUser, User
from app.models.enums import TenantRole
from app.services import referral


async def _owned_tenant(s, *, tg: int, slug: str, referred_by=None) -> tuple[User, Tenant]:
    owner = User(telegram_id=tg, first_name="U", referred_by_user_id=referred_by)
    s.add(owner)
    await s.flush()
    t = Tenant(slug=slug, name=slug)
    s.add(t)
    await s.flush()
    s.add(TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner))
    await s.flush()
    return owner, t


def _enable(s, reward: str = "50000") -> None:
    s.add(PlatformSetting(key="referral", value={"enabled": True, "reward_value": reward}))


async def test_dormant_then_happy_then_idempotent(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        referrer, ref_tenant = await _owned_tenant(s, tg=9610, slug="referrer")
        _, referee_tenant = await _owned_tenant(s, tg=9620, slug="referee", referred_by=referrer.id)

        # 1) programme dormant (no config) -> no-op, no money
        assert await referral.maybe_award_referral_conversion(s, referee_tenant) is None
        assert (await s.execute(select(Referral.id))).first() is None

        _enable(s)
        await s.flush()

        # 2) happy path -> Referral(converted) + referrer tenant credited
        r = await referral.maybe_award_referral_conversion(s, referee_tenant)
        assert r is not None and r.status == "converted" and r.credit_amount == Decimal("50000")
        assert ref_tenant.balance == Decimal("50000")              # same identity-map instance

        # 3) idempotent -> second call no-ops, no double credit
        assert await referral.maybe_award_referral_conversion(s, referee_tenant) is None
        assert ref_tenant.balance == Decimal("50000")
        assert len((await s.execute(select(Referral))).scalars().all()) == 1


async def test_not_referred_is_noop(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        _enable(s)
        _, organic = await _owned_tenant(s, tg=9630, slug="organic")   # no referred_by
        await s.flush()
        assert await referral.maybe_award_referral_conversion(s, organic) is None
        assert (await s.execute(select(TenantBalanceTxn.id))).first() is None


async def test_referrer_without_tenant_records_no_payout(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        _enable(s)
        referrer = User(telegram_id=9640, first_name="NoTenant")        # owns no tenant
        s.add(referrer)
        await s.flush()
        _, referee_tenant = await _owned_tenant(s, tg=9650, slug="referee2", referred_by=referrer.id)
        await s.flush()
        r = await referral.maybe_award_referral_conversion(s, referee_tenant)
        assert r is not None and r.status == "no_payout" and r.credit_amount == Decimal("0")
        assert (await s.execute(select(TenantBalanceTxn.id))).first() is None   # nothing credited
