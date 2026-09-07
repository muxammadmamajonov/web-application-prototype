"""(#853) Concurrent tenant-balance CREDITS must not lose an update.

Two credit paths read-modify-write ``tenant.balance`` and must serialize on a
``FOR UPDATE`` lock, else two overlapping credits both read the same balance and one
is lost (the tenant/referrer is short-changed):

* platform admin top-up (``POST /api/platform/tenants/{id}/balance`` → ``_apply_balance``)
* referral conversion bonus (``maybe_award_referral_conversion`` → referrer's tenant)

Both tests widen the lock-hold window so the second writer genuinely contends on the
Postgres row lock (the in-process harness otherwise serializes them and the race never
manifests). Each is teeth-checked: without the lock the final balance is one credit
short; with it, both credits land.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

_AMOUNT = 50_000


async def test_concurrent_admin_credits_dont_lose_update(migrated_db, monkeypatch) -> None:
    from app.core.security import issue_token
    from app.db import get_db
    from app.main import app
    from app.models import Tenant, TenantBalanceTxn, User
    from app.models.enums import PlatformRole, TenantStatus
    from app.services import audit

    # Widen: the audit write runs after the credit, while the tenant row is still locked
    # (before commit), so yielding here forces the second request to contend on the lock.
    async def _slow_record(*_a, **_k):
        await asyncio.sleep(0.25)

    monkeypatch.setattr(audit, "record", _slow_record)

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        admin = User(telegram_id=853001, platform_role=PlatformRole.super_admin)
        t = Tenant(slug="p853-plat", name="P853 Plat", plan_code="trial",
                   status=TenantStatus.active, balance=Decimal(0))
        s.add_all([admin, t])
        await s.flush()
        tid, aid = t.id, admin.id
        await s.commit()

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    auth = {"Authorization": f"Bearer {issue_token(user_id=str(aid))}"}
    try:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            async def _credit():
                return await client.post(
                    f"/api/platform/tenants/{tid}/balance", headers=auth, json={"amount": _AMOUNT},
                )
            r1, r2 = await asyncio.gather(_credit(), _credit())
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r1.status_code == 200 and r2.status_code == 200, (
        f"both credits should succeed — got {r1.status_code}/{r2.status_code}: "
        f"{r1.text[:120]} | {r2.text[:120]}"
    )
    async with Session() as s:
        bal = (await s.execute(select(Tenant.balance).where(Tenant.id == tid))).scalar_one()
        assert int(bal) == 2 * _AMOUNT, (
            f"both admin credits must apply (no lost update): expected {2 * _AMOUNT}, got {bal}"
        )
        n = (await s.execute(select(func.count()).select_from(TenantBalanceTxn).where(
            TenantBalanceTxn.tenant_id == tid, TenantBalanceTxn.kind == "topup",
        ))).scalar_one()
        assert n == 2, f"two topup ledger rows, got {n}"


async def test_concurrent_referral_conversions_dont_lose_update(migrated_db) -> None:
    from app.models import (
        PlatformSetting,
        Referral,
        Tenant,
        TenantBalanceTxn,
        TenantUser,
        User,
    )
    from app.models.enums import TenantRole, TenantStatus
    from app.services.referral import REFERRAL_KEY, maybe_award_referral_conversion

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        s.add(PlatformSetting(key=REFERRAL_KEY, value={
            "enabled": True, "reward_kind": "balance_credit", "reward_value": str(_AMOUNT),
        }))
        referrer = User(telegram_id=853101, ref_code="P853REF")
        s.add(referrer)
        await s.flush()
        rt = Tenant(slug="p853-referrer", name="Referrer", plan_code="trial",
                    status=TenantStatus.active, balance=Decimal(0))
        s.add(rt)
        await s.flush()
        s.add(TenantUser(tenant_id=rt.id, user_id=referrer.id, role=TenantRole.owner))
        # Two referees, each owned by a distinct user referred by the SAME referrer.
        referee_tids = []
        for i in (1, 2):
            ro = User(telegram_id=853110 + i, referred_by_user_id=referrer.id)
            s.add(ro)
            await s.flush()
            rtt = Tenant(slug=f"p853-referee{i}", name=f"Referee{i}", plan_code="trial",
                         status=TenantStatus.active, balance=Decimal(0))
            s.add(rtt)
            await s.flush()
            s.add(TenantUser(tenant_id=rtt.id, user_id=ro.id, role=TenantRole.owner))
            referee_tids.append(rtt.id)
        rt_id = rt.id
        await s.commit()

    # Two concurrent conversions (different referees → same referrer) on separate sessions.
    # The sleep-before-commit holds the locked referrer row so the second conversion must
    # contend; without the lock both read balance=0 before either commits → lost update.
    async def _convert(referee_tid):
        async with Session() as s:
            referee = (await s.execute(
                select(Tenant).where(Tenant.id == referee_tid)
            )).scalar_one()
            await maybe_award_referral_conversion(s, referee)
            await asyncio.sleep(0.25)
            await s.commit()

    await asyncio.gather(_convert(referee_tids[0]), _convert(referee_tids[1]))

    async with Session() as s:
        bal = (await s.execute(select(Tenant.balance).where(Tenant.id == rt_id))).scalar_one()
        assert int(bal) == 2 * _AMOUNT, (
            f"both referral rewards must apply (no lost update): expected {2 * _AMOUNT}, got {bal}"
        )
        bonuses = (await s.execute(select(func.count()).select_from(TenantBalanceTxn).where(
            TenantBalanceTxn.tenant_id == rt_id, TenantBalanceTxn.kind == "referral_bonus",
        ))).scalar_one()
        assert bonuses == 2, f"two referral_bonus ledger rows, got {bonuses}"
        converted = (await s.execute(select(func.count()).select_from(Referral).where(
            Referral.status == "converted",
        ))).scalar_one()
        assert converted == 2, f"two converted referrals, got {converted}"
