"""Referral programme configuration + reward economics (#196).

The referral *infrastructure* (the `Referral` row, `User.ref_code`, the
`?startapp=ref_<code>` deep link) shipped earlier but was inert: no bonus was
ever awarded and nothing was tunable. This module is the configurable economics
layer, stored in the ``referral`` platform setting (key/value, like
``demo_plan``) so the owner can tune it without a deploy.

**Default-off:** ``enabled=False`` / ``reward_value="0"`` means the programme is
dormant until the owner deliberately configures it — shipping this never moves
money on its own. The award path (services that read this) must early-return
when ``enabled`` is false.

Economic knobs (set by the platform owner in the settings UI):

* ``enabled``      master switch.
* ``reward_kind``  how the bonus is paid:
                   - ``balance_credit`` → credit the recipient's platform
                     balance via the tenant ledger (reduces their next invoice);
                   - ``free_days``      → extend the recipient's paid plan.
* ``reward_value`` amount — UZS for ``balance_credit``, days for ``free_days``.
                   Stored as a STRING so JSON keeps exact Decimal precision.
* ``reward_to``    who is rewarded: ``referrer`` | ``referee`` | ``both``.
* ``trigger``      when the referral converts and the bonus is awarded:
                   ``first_paid_subscription`` (the referee tenant's first paid
                   plan clears) | ``referee_signup`` (riskier — pays on signup).
* ``max_per_referrer`` cap on how many rewarded conversions one referrer can
                   earn (``None`` = uncapped); abuse guard.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    PlatformSetting,
    Referral,
    Tenant,
    TenantBalanceTxn,
    TenantUser,
    User,
)
from app.models.enums import TenantRole

log = logging.getLogger("muddato.referral")

REFERRAL_KEY = "referral"

DEFAULTS: dict[str, Any] = {
    "enabled": False,                       # dormant until the owner turns it on
    "reward_kind": "balance_credit",        # balance_credit | free_days
    "reward_value": "0",                    # UZS (balance_credit) or days (free_days)
    "reward_to": "referrer",                # referrer | referee | both
    "trigger": "first_paid_subscription",   # first_paid_subscription | referee_signup
    "max_per_referrer": None,               # None = uncapped
}

_REWARD_KINDS = {"balance_credit", "free_days"}
_REWARD_TO = {"referrer", "referee", "both"}
_TRIGGERS = {"first_paid_subscription", "referee_signup"}


async def get_config(db: AsyncSession) -> dict[str, Any]:
    """Merge stored overrides over DEFAULTS (same idiom as demo_plan.get_config)."""
    row = (await db.execute(
        select(PlatformSetting).where(PlatformSetting.key == REFERRAL_KEY)
    )).scalar_one_or_none()
    cfg = dict(DEFAULTS)
    if row and isinstance(row.value, dict):
        for k in DEFAULTS:
            if k in row.value:
                cfg[k] = row.value[k]
    return cfg


def reward_amount(cfg: dict[str, Any]) -> Decimal:
    """Parse ``reward_value`` to a non-negative Decimal; 0 on garbage/blank.

    Kept tolerant on purpose: a malformed stored value must degrade to "no
    bonus", never raise inside an award/payment path."""
    try:
        v = Decimal(str(cfg.get("reward_value", "0")))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")
    return v if v > 0 else Decimal("0")


def is_active(cfg: dict[str, Any]) -> bool:
    """True only when the programme is enabled AND would actually pay out — the
    single guard every award path should check before doing anything."""
    return bool(cfg.get("enabled")) and reward_amount(cfg) > 0


def validate_overrides(patch: dict[str, Any]) -> dict[str, str]:
    """Validate an owner-supplied config patch; return {field: error} (empty = ok).

    Surfaced by the settings endpoint so a bad economics config is rejected at
    write time rather than silently misbehaving when a referral converts."""
    errs: dict[str, str] = {}
    if "reward_kind" in patch and patch["reward_kind"] not in _REWARD_KINDS:
        errs["reward_kind"] = f"must be one of {sorted(_REWARD_KINDS)}"
    if "reward_to" in patch and patch["reward_to"] not in _REWARD_TO:
        errs["reward_to"] = f"must be one of {sorted(_REWARD_TO)}"
    if "trigger" in patch and patch["trigger"] not in _TRIGGERS:
        errs["trigger"] = f"must be one of {sorted(_TRIGGERS)}"
    if "reward_value" in patch:
        try:
            if Decimal(str(patch["reward_value"])) < 0:
                errs["reward_value"] = "must be >= 0"
        except (InvalidOperation, ValueError, TypeError):
            errs["reward_value"] = "must be a number"
    if "max_per_referrer" in patch and patch["max_per_referrer"] is not None:
        v = patch["max_per_referrer"]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            errs["max_per_referrer"] = "must be a positive integer or null"
    return errs


async def record_referral_intent(db: AsyncSession, referee: User, ref_code: str) -> bool:
    """(#196 capture) Stamp who referred ``referee``, from a ?startapp=ref_<code> link.

    Idempotent + guarded — ignores a blank/unknown code, self-referral, and a referee
    who is already attributed (first referrer wins, so a later link can't hijack the
    credit). Returns True iff it stamped. The caller commits. No money moves here; the
    reward is decided later, at conversion, against the (possibly-changed) config."""
    code = (ref_code or "").strip()
    if not code or referee.referred_by_user_id is not None:
        return False
    referrer = (await db.execute(
        select(User).where(User.ref_code == code)
    )).scalar_one_or_none()
    if referrer is None or referrer.id == referee.id:
        return False                       # unknown code or self-referral
    referee.referred_by_user_id = referrer.id
    return True


async def _owner_of(db: AsyncSession, tenant_id) -> User | None:
    return (await db.execute(
        select(User).join(TenantUser, TenantUser.user_id == User.id).where(
            TenantUser.tenant_id == tenant_id, TenantUser.role == TenantRole.owner,
        ).limit(1)
    )).scalar_one_or_none()


async def maybe_award_referral_conversion(db: AsyncSession, referee_tenant: Tenant) -> Referral | None:
    """(#196 conversion) Credit the referrer when a referred tenant becomes paying.

    IDEMPOTENT — at most one Referral per referee tenant, so a retried/duplicated
    billing run can never double-pay. GATED on referral.is_active, so it's a cheap
    early-return (and moves no money) until the owner enables the programme and sets a
    payout. Returns the Referral if recorded, else None. The caller commits.

    Reward goes to the referrer's primary (oldest) owned tenant's balance via the
    ledger (mirrors api.platform._apply_balance — inlined to avoid an api<-service
    import cycle). A non-balance reward_kind, or a referrer with no creditable tenant,
    is recorded as ``no_payout`` so it's auditable and never retried.

    (F-04) The per-referee SELECT below is only a fast path: under READ COMMITTED two
    concurrent conversions of the SAME referee both pass it (neither sees the other's
    uncommitted row), and the #853 ``FOR UPDATE`` locks the *referrer*, not the referee
    — so it does not serialize them. The real guard is the partial
    ``uq_referrals_referee_tenant`` unique index: the row insert is staged inside a
    savepoint and a unique violation is treated as an idempotent no-op (the conversion
    already happened on the racing transaction), reverting the credit with it so the
    referrer is never paid twice.
    """
    # Already converted for this referee tenant? -> idempotent no-op (fast path).
    if (await db.execute(
        select(Referral.id).where(Referral.referee_tenant_id == referee_tenant.id)
    )).first() is not None:
        return None

    owner = await _owner_of(db, referee_tenant.id)
    if owner is None or owner.referred_by_user_id is None or owner.referred_by_user_id == owner.id:
        return None                                    # not referred (or self)

    cfg = await get_config(db)
    if not is_active(cfg):
        return None                                    # programme dormant
    reward = reward_amount(cfg)

    referrer_tenant = (await db.execute(
        select(Tenant).join(TenantUser, TenantUser.tenant_id == Tenant.id).where(
            TenantUser.user_id == owner.referred_by_user_id,
            TenantUser.role == TenantRole.owner,
        ).order_by(Tenant.created_at.asc()).limit(1)
    )).scalar_one_or_none()

    if referrer_tenant is None or cfg.get("reward_kind") != "balance_credit":
        ref = Referral(
            referrer_user_id=owner.referred_by_user_id,
            referee_tenant_id=referee_tenant.id,
            status="no_payout", credit_amount=Decimal("0"),
            notes=("referrer has no tenant" if referrer_tenant is None
                   else f"reward_kind={cfg.get('reward_kind')} not auto-credited"),
        )
        return await _insert_referral(db, ref, referee_tenant.id)

    # (#853) Lock the referrer tenant row before the balance read+credit. The
    # idempotency guard above is per-REFEREE, but the contended row is the shared
    # REFERRER: two different referees of the same referrer converting concurrently
    # would otherwise both read the same balance and lose an update. Same FOR UPDATE
    # pattern as mark_topup_paid (#116); refresh upgrades the unlocked SELECT above.
    await db.refresh(referrer_tenant, with_for_update=True)

    # (#865) Enforce the max_per_referrer cap: once the referrer has earned that many
    # rewarded (converted) conversions, record further ones as no_payout (auditable,
    # moves no money) instead of crediting. Counted under the referrer-tenant lock above,
    # so concurrent conversions for the same referrer can't both slip past the cap. An
    # unset/None cap stays uncapped (the default).
    cap = cfg.get("max_per_referrer")
    if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
        rewarded = (await db.execute(
            select(func.count()).select_from(Referral).where(
                Referral.referrer_user_id == owner.referred_by_user_id,
                Referral.status == "converted",
            )
        )).scalar_one()
        if rewarded >= cap:
            ref = Referral(
                referrer_user_id=owner.referred_by_user_id,
                referee_tenant_id=referee_tenant.id,
                status="no_payout", credit_amount=Decimal("0"),
                notes=f"max_per_referrer cap ({cap}) reached",
            )
            return await _insert_referral(db, ref, referee_tenant.id)

    # Pay the bonus into the referrer tenant's balance (mirrors _apply_balance). The
    # balance bump, the ledger row and the Referral are staged together inside one
    # savepoint so a per-referee unique violation (a racing conversion that already
    # paid) reverts ALL THREE — no credit without a row, no row without a credit.
    ref = Referral(
        referrer_user_id=owner.referred_by_user_id,
        referee_tenant_id=referee_tenant.id,
        status="converted", credit_amount=reward,
    )

    def _apply_credit() -> None:
        referrer_tenant.balance = (referrer_tenant.balance or Decimal(0)) + reward
        db.add(TenantBalanceTxn(
            tenant_id=referrer_tenant.id, amount=reward, kind="referral_bonus",
            note=f"Referral bonus — referee tenant {referee_tenant.id}",
            balance_after=referrer_tenant.balance,
            created_by_user_id=owner.referred_by_user_id,
        ))
        db.add(ref)

    return await _insert_referral(db, ref, referee_tenant.id, stage=_apply_credit)


async def _insert_referral(
    db: AsyncSession,
    ref: Referral,
    referee_tenant_id,
    *,
    stage=None,
) -> Referral | None:
    """Stage ``ref`` (plus any ``stage()`` side effects, e.g. the balance credit)
    inside a savepoint and flush. (F-04) A unique violation on
    ``uq_referrals_referee_tenant`` means a concurrent conversion already recorded this
    referee — roll the savepoint back (reverting the credit too) and return None so the
    caller treats it as the idempotent no-op the fast-path SELECT intended. The caller
    still owns the outer commit; rolling back to the savepoint keeps that transaction
    usable (its prior locks/state survive)."""
    try:
        async with db.begin_nested():
            if stage is not None:
                stage()
            else:
                db.add(ref)
            await db.flush()
    except IntegrityError:
        log.info(
            "referral conversion lost a race (already recorded) — no-op",
            extra={"referee_tenant_id": str(referee_tenant_id)},
        )
        return None
    return ref
