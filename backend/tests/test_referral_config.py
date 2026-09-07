"""(#196) Referral programme config: default-off, override merge, tolerant parsing."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import PlatformSetting
from app.services import referral


def test_reward_amount_is_tolerant() -> None:
    # garbage / blank / negative all degrade to 0 — never raise in a money path
    assert referral.reward_amount({"reward_value": "50000"}) == Decimal("50000")
    assert referral.reward_amount({"reward_value": "abc"}) == Decimal("0")
    assert referral.reward_amount({"reward_value": "-10"}) == Decimal("0")
    assert referral.reward_amount({}) == Decimal("0")


def test_is_active_requires_enabled_and_payout() -> None:
    assert referral.is_active({"enabled": True, "reward_value": "1000"}) is True
    assert referral.is_active({"enabled": True, "reward_value": "0"}) is False   # enabled but pays nothing
    assert referral.is_active({"enabled": False, "reward_value": "1000"}) is False
    assert referral.is_active(dict(referral.DEFAULTS)) is False                  # defaults = dormant


def test_validate_overrides() -> None:
    assert referral.validate_overrides({"enabled": True, "reward_value": "5000"}) == {}
    bad = referral.validate_overrides({
        "reward_kind": "bitcoin", "reward_to": "nobody",
        "trigger": "whenever", "reward_value": "-1", "max_per_referrer": 0,
    })
    assert set(bad) == {"reward_kind", "reward_to", "trigger", "reward_value", "max_per_referrer"}


async def test_get_config_defaults_and_override(migrated_db) -> None:
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        # empty DB -> DEFAULTS (dormant)
        cfg = await referral.get_config(s)
        assert cfg["enabled"] is False and referral.is_active(cfg) is False

        # owner override merges over defaults
        s.add(PlatformSetting(key=referral.REFERRAL_KEY,
                              value={"enabled": True, "reward_value": "75000"}))
        await s.commit()
        cfg = await referral.get_config(s)
        assert cfg["enabled"] is True
        assert referral.reward_amount(cfg) == Decimal("75000")
        assert cfg["reward_to"] == "referrer"        # untouched default still present
        assert referral.is_active(cfg) is True
