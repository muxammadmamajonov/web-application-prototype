"""(#865) The referral ``max_per_referrer`` cap must stop rewarding a referrer once they've
earned that many converted conversions — otherwise the configured cap is a no-op and the
platform over-pays referral bonuses.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

_REWARD = 50_000


async def _setup(Session, *, max_per_referrer):
    """A referrer (+ one owned tenant) and the referral programme config. Returns
    ``(referrer_id, referrer_tenant_id)``."""
    from app.models import PlatformSetting, Tenant, TenantUser, User
    from app.models.enums import TenantRole, TenantStatus
    from app.services.referral import REFERRAL_KEY

    value: dict = {"enabled": True, "reward_kind": "balance_credit", "reward_value": str(_REWARD)}
    if max_per_referrer is not None:
        value["max_per_referrer"] = max_per_referrer
    async with Session() as s:
        s.add(PlatformSetting(key=REFERRAL_KEY, value=value))
        referrer = User(telegram_id=865001, ref_code="P865REF")
        s.add(referrer)
        await s.flush()
        rt = Tenant(slug="p865-referrer", name="Referrer", plan_code="trial",
                    status=TenantStatus.active, balance=Decimal(0))
        s.add(rt)
        await s.flush()
        s.add(TenantUser(tenant_id=rt.id, user_id=referrer.id, role=TenantRole.owner))
        ids = (referrer.id, rt.id)
        await s.commit()
    return ids


async def _make_and_convert(Session, referrer_id, idx: int) -> str | None:
    """Create a referee (owner referred_by the referrer) + tenant, run the conversion,
    and return the resulting Referral status."""
    from app.models import Tenant, TenantUser, User
    from app.models.enums import TenantRole, TenantStatus
    from app.services.referral import maybe_award_referral_conversion

    async with Session() as s:
        owner = User(telegram_id=865100 + idx, referred_by_user_id=referrer_id)
        s.add(owner)
        await s.flush()
        rtt = Tenant(slug=f"p865-referee{idx}", name=f"Referee{idx}", plan_code="trial",
                     status=TenantStatus.active, balance=Decimal(0))
        s.add(rtt)
        await s.flush()
        s.add(TenantUser(tenant_id=rtt.id, user_id=owner.id, role=TenantRole.owner))
        await s.flush()
        ref = await maybe_award_referral_conversion(s, rtt)
        status = ref.status if ref is not None else None
        await s.commit()
    return status


async def _balance(Session, tid) -> int:
    from app.models import Tenant

    async with Session() as s:
        return int((await s.execute(select(Tenant.balance).where(Tenant.id == tid))).scalar_one())


async def test_referral_cap_stops_rewarding_past_max(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    referrer_id, referrer_tenant_id = await _setup(Session, max_per_referrer=1)

    assert await _make_and_convert(Session, referrer_id, 1) == "converted"
    assert await _balance(Session, referrer_tenant_id) == _REWARD, \
        "the 1st conversion (under the cap) must be rewarded"

    assert await _make_and_convert(Session, referrer_id, 2) == "no_payout"
    assert await _balance(Session, referrer_tenant_id) == _REWARD, \
        "the 2nd conversion must hit max_per_referrer=1 — no extra credit"


async def test_referral_uncapped_when_no_cap(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    referrer_id, referrer_tenant_id = await _setup(Session, max_per_referrer=None)

    assert await _make_and_convert(Session, referrer_id, 1) == "converted"
    assert await _make_and_convert(Session, referrer_id, 2) == "converted"
    assert await _balance(Session, referrer_tenant_id) == 2 * _REWARD, \
        "with no cap configured, both conversions are rewarded"
