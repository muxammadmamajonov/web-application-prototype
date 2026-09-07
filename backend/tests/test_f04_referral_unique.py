"""F-04 — referral bonus double-pay is prevented by a DB unique constraint.

The audit (FABLE.md F-04) found that "at most one Referral per referee tenant" was
enforced only by an application SELECT in ``maybe_award_referral_conversion`` with no
DB unique constraint. Under READ COMMITTED two concurrent conversions of the SAME
referee both pass that unlocked SELECT and both credit the referrer — real money paid
twice. The #853 ``FOR UPDATE`` locks the *referrer*, so it serializes two *different*
referees of one referrer; it does NOT serialize two conversions of one referee.

The fix is a partial ``UNIQUE(referee_tenant_id) WHERE referee_tenant_id IS NOT NULL``
(``uq_referrals_referee_tenant``) plus an IntegrityError-as-no-op handler that stages
the credit + the Referral insert in one savepoint, so a lost race reverts BOTH.

Two tiers, mirroring test_payment_integrity.py:
  * pure-logic (always runs): the model actually declares the partial unique index.
  * DB-backed (skips without TEST_DATABASE_URL; the CI `integration` job provides PG):
    the constraint rejects a duplicate, and two CONCURRENT conversions credit once.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Referral

# ── pure-logic: the model declares the partial unique index (no DB needed) ───────


def _referee_unique_index():
    """The partial unique Index on Referral.referee_tenant_id, or None."""
    for ix in Referral.__table__.indexes:
        if ix.name == "uq_referrals_referee_tenant":
            return ix
    return None


def test_referral_model_has_partial_unique_on_referee_tenant() -> None:
    """(F-04) The DB-level guard exists in the ORM metadata: a UNIQUE index on
    referee_tenant_id, partial on ``referee_tenant_id IS NOT NULL`` so the nullable
    FK column still allows many unattributed (NULL) rows."""
    ix = _referee_unique_index()
    assert ix is not None, "uq_referrals_referee_tenant index missing from model"
    assert ix.unique is True
    assert [c.name for c in ix.columns] == ["referee_tenant_id"]
    predicate = str(ix.dialect_options["postgresql"]["where"])
    assert "referee_tenant_id IS NOT NULL" in predicate


def test_referee_tenant_id_has_no_separate_plain_index() -> None:
    """The bare ``index=True`` was dropped: the unique index above already serves
    referee lookups, so there must not be a second redundant index on the column."""
    plain = [
        ix for ix in Referral.__table__.indexes
        if [c.name for c in ix.columns] == ["referee_tenant_id"] and not ix.unique
    ]
    assert plain == [], f"redundant non-unique index on referee_tenant_id: {plain}"


# ── DB-backed: real Postgres enforces it (skips without TEST_DATABASE_URL) ───────


async def _seed_referred_conversion(
    engine, *, reward="50000", enabled=True, max_per_referrer=None,
):
    """Seed a referrer (with an owned tenant) + a referee tenant whose owner was
    referred by the referrer, and configure the referral programme. Returns the
    referee Tenant id. Mirrors the conversion preconditions in referral.py."""
    from app.models import PlatformSetting, Tenant, TenantUser, User
    from app.models.enums import TenantRole
    from app.services.referral import REFERRAL_KEY

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        referrer = User(telegram_id=900001, ref_code="REFER01")
        referee_owner = User(telegram_id=900002)
        s.add_all([referrer, referee_owner])
        await s.flush()
        referee_owner.referred_by_user_id = referrer.id

        # Referrer's own (oldest) owned tenant — the credit target.
        rt = Tenant(slug="f04-referrer", name="Referrer Co", balance=Decimal("0"))
        # The referee's tenant that just became paying.
        et = Tenant(slug="f04-referee", name="Referee Co")
        s.add_all([rt, et])
        await s.flush()
        s.add_all([
            TenantUser(tenant_id=rt.id, user_id=referrer.id, role=TenantRole.owner),
            TenantUser(tenant_id=et.id, user_id=referee_owner.id, role=TenantRole.owner),
        ])
        s.add(PlatformSetting(key=REFERRAL_KEY, value={
            "enabled": enabled,
            "reward_kind": "balance_credit",
            "reward_value": reward,
            "reward_to": "referrer",
            "trigger": "first_paid_subscription",
            "max_per_referrer": max_per_referrer,
        }))
        await s.commit()
        return str(rt.id), str(et.id)


async def test_db_rejects_second_referral_for_same_referee(migrated_db) -> None:
    """(F-04) The partial unique index is live in the real schema: a second Referral
    row for the same referee_tenant_id raises IntegrityError. Two NULL-referee rows
    coexist (the index is partial), proving it doesn't over-constrain."""
    from app.models import Tenant, User

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        u = User(telegram_id=910001)
        t = Tenant(slug="f04-dup", name="Dup Co")
        s.add_all([u, t])
        await s.flush()
        s.add(Referral(referrer_user_id=u.id, referee_tenant_id=t.id, status="converted"))
        await s.commit()
        uid, tid = str(u.id), str(t.id)

    with pytest.raises(IntegrityError):
        async with Session() as s:
            s.add(Referral(referrer_user_id=uid, referee_tenant_id=tid, status="converted"))
            await s.commit()

    # Partial: many NULL-referee rows are still allowed (e.g. no_payout audit rows).
    async with Session() as s:
        s.add_all([
            Referral(referrer_user_id=uid, referee_tenant_id=None, status="no_payout"),
            Referral(referrer_user_id=uid, referee_tenant_id=None, status="no_payout"),
        ])
        await s.commit()  # must NOT raise


async def test_concurrent_conversions_credit_referrer_once(migrated_db) -> None:
    """The core F-04 invariant under TRUE concurrency: two overlapping
    ``maybe_award_referral_conversion`` calls for the SAME referee credit the referrer
    exactly once — one Referral row, one ledger entry, balance == a single reward.
    Without the unique index + IntegrityError-as-no-op this credits twice."""
    import asyncio

    from app.models import Tenant, TenantBalanceTxn
    from app.services.referral import maybe_award_referral_conversion

    rt_id, et_id = await _seed_referred_conversion(migrated_db, reward="50000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async def convert():
        async with Session() as s:
            referee = await s.get(Tenant, et_id)
            ref = await maybe_award_referral_conversion(s, referee)
            await s.commit()
            return ref

    await asyncio.gather(convert(), convert(), return_exceptions=True)

    async with Session() as s:
        rt = await s.get(Tenant, rt_id)
        n_ref = (await s.execute(
            select(func.count(Referral.id)).where(Referral.referee_tenant_id == et_id)
        )).scalar_one()
        n_txn = (await s.execute(
            select(func.count(TenantBalanceTxn.id)).where(
                TenantBalanceTxn.tenant_id == rt_id,
                TenantBalanceTxn.kind == "referral_bonus",
            )
        )).scalar_one()
    assert n_ref == 1                          # exactly one Referral for the referee
    assert n_txn == 1                          # exactly one ledger credit
    assert rt.balance == Decimal("50000")      # credited once, not 100000


async def test_sequential_retry_after_commit_is_noop(migrated_db) -> None:
    """The sequential analogue: a second conversion AFTER the first committed reads
    the existing Referral via the fast-path SELECT and no-ops — the referrer is not
    credited again (a duplicated/redelivered billing run can't double-pay)."""
    from app.models import Tenant, TenantBalanceTxn
    from app.services.referral import maybe_award_referral_conversion

    rt_id, et_id = await _seed_referred_conversion(migrated_db, reward="50000")
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:  # first conversion — credits + commits the Referral
        first = await maybe_award_referral_conversion(s, await s.get(Tenant, et_id))
        await s.commit()
    async with Session() as s:  # retry after the first committed
        retry = await maybe_award_referral_conversion(s, await s.get(Tenant, et_id))
        await s.commit()

    assert first is not None and first.status == "converted"
    assert retry is None                       # fast-path SELECT short-circuits
    async with Session() as s:
        rt = await s.get(Tenant, rt_id)
        n_txn = (await s.execute(
            select(func.count(TenantBalanceTxn.id)).where(
                TenantBalanceTxn.tenant_id == rt_id,
                TenantBalanceTxn.kind == "referral_bonus",
            )
        )).scalar_one()
    assert rt.balance == Decimal("50000")      # not 100000
    assert n_txn == 1
