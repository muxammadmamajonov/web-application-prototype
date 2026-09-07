"""Invitation tokens for customer / staff onboarding.

The tenant calls ``POST /tenant/invitations`` to mint a one-shot token,
then shares the resulting URL (web or Telegram deep-link). The recipient
opens it, fills the Mini-App form, and ``POST /m/<slug>/i/<token>/claim``
materialises the Customer / TenantUser.

URL shapes
==========

* `bot_mode == "shared"` (Trial/Lite plans):
    ``https://t.me/MuddatoBot?start=inv_<token>``

* `bot_mode == "tenant"` (Pro/Custom):
    ``https://t.me/<tenant_bot_username>?start=inv_<token>``

* Web fallback (also opens the Mini-App when clicked from a non-Telegram
  browser):
    ``https://muddato.uz/m/<tenant-slug>/i/<token>``
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tenant import _require_owner_or_manager, resolve_tenant
from app.config import settings
from app.core import invite_codes
from app.core.ratelimit import invite_limit, public_form_limit
from app.core.validators import UzPhoneOpt
from app.db import get_db
from app.models import (
    Customer,
    Guarantor,
    Installment,
    InstallmentGuarantor,
    Invitation,
    Plan,
    Tenant,
    TenantSettings,
    TenantUser,
)
from app.models.enums import CustomerStatus, TenantRole

router = APIRouter(tags=["invitations"])

INV_TTL_HOURS = 24 * 7      # invitation lives for a week by default


async def _tenant_bot_username(db: AsyncSession, tenant: Tenant) -> str:
    """Resolve which bot username to use for the deep-link.

    Returns the tenant's own bot if their plan permits it AND they've
    activated their bot; otherwise the platform's @MuddatoBot.
    """
    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )).scalar_one_or_none()
    plan = (await db.execute(
        select(Plan).where(Plan.code == tenant.plan_code)
    )).scalar_one_or_none()
    effective_mode = (s.bot_mode if s else "shared") or "shared"
    plan_allows = (plan.bot_mode if plan else "shared") == "tenant"
    if effective_mode == "tenant" and plan_allows and s and s.bot_username and s.bot_activated_at:
        return s.bot_username
    return settings.telegram_bot_username


# ── tenant-side: mint / list / revoke ──────────────────────────────────

class CreateInvitationIn(BaseModel):
    kind:    Literal["customer", "staff", "guarantor"]
    role:    Literal["owner", "manager", "sales", "accountant", "custom"] | None = None
    preset:  dict = Field(default_factory=dict)   # e.g. {"product_id": "...", "model_id": "..."}
    expires_hours: int = Field(default=INV_TTL_HOURS, ge=1, le=720)


class InvitationOut(BaseModel):
    """An invitation as the dashboard sees it.

    The credential and its links are present ONLY in the response that mints
    them. Storage keeps a verifier, so there is nothing to show later even if a
    caller asks — a lost link is revoked and reissued, not recovered.
    """

    id:           str
    token:        str | None = None
    kind:         str
    role:         str | None
    web_url:      str | None = None
    telegram_url: str | None = None
    expires_at:   str
    used_at:      str | None
    created_at:   str


def _serialize(
    inv: Invitation, tenant_slug: str, bot_username: str, raw: str | None = None,
) -> InvitationOut:
    # tenant_slug is left in the comment for future analytics / branding;
    # the URL itself is slug-less because the credential identifies the tenant
    # via the Invitation row. Less to break, simpler links.
    #
    # `raw` is supplied only by the mint path. Without it there are no links,
    # because the credential cannot be derived from what is stored.
    _ = tenant_slug
    return InvitationOut(
        id=str(inv.id),
        token=raw,
        kind=inv.kind,
        role=inv.role,
        web_url=invite_codes.web_url(raw) if raw else None,
        telegram_url=invite_codes.deep_link(bot_username, raw) if raw else None,
        expires_at=inv.expires_at.isoformat(),
        used_at=inv.used_at.isoformat() if inv.used_at else None,
        created_at=inv.created_at.isoformat() if inv.created_at else None,
    )


@router.post("/tenant/invitations", status_code=status.HTTP_201_CREATED, response_model=InvitationOut, dependencies=[invite_limit()])
async def create_invitation(
    body: CreateInvitationIn,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InvitationOut:
    tenant, membership = ctx
    # (Batch 6) Same table as every other mint site. This route already had the
    # right rules — owner-or-manager, plus an owner-only ceiling on granting an
    # owner seat — and they are unchanged in effect; they simply no longer live
    # here alone, where the two customer-invite routes could not see them.
    from app.core.invite_policy import MintSurface, require_may_mint

    require_may_mint(membership, body.kind, surface=MintSurface.console,
                     granted_role=body.role)

    # A 192-bit credential does not collide, but a concurrent insert is still
    # the general case the UNIQUE index exists for, so the retry stays.
    for attempt in range(3):
        raw_code, code_verifier = await invite_codes.mint(db)
        inv = Invitation(
            token_verifier=code_verifier,
            tenant_id=tenant.id,
            kind=body.kind,
            role=body.role,
            preset=body.preset,
            created_by_user_id=membership.user_id,
            expires_at=datetime.now(UTC) + timedelta(hours=body.expires_hours),
        )
        try:
            # SAVEPOINT, not a plain flush: a bare `db.rollback()` on collision
            # would discard the whole request transaction and expire `tenant`
            # and `membership`, so the next attribute read would lazy-load
            # outside the greenlet and raise. Only the INSERT unwinds.
            async with db.begin_nested():
                db.add(inv)
            break
        except IntegrityError:
            if attempt == 2:
                raise
    
    from app.services import audit
    await audit.record(
        db, action="invitation.create", tenant_id=tenant.id,
        actor_user_id=membership.user_id, entity_type="invitation",
        entity_id=str(inv.id),
        after={"kind": body.kind, "role": body.role},
    )
    await db.commit()
    await db.refresh(inv)

    bot_user = await _tenant_bot_username(db, tenant)
    return _serialize(inv, tenant.slug, bot_user, raw=raw_code)


@router.get("/tenant/invitations")
async def list_invitations(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    kind: Literal["customer", "staff", "guarantor"] | None = None,
    only_open: bool = True,
) -> list[InvitationOut]:
    tenant, membership = ctx
    # (sec) Each row carries the raw claim token and its ready-made links, so
    # reading this list is equivalent to holding every credential in it. Gate the
    # read exactly as the mint path is gated.
    _require_owner_or_manager(membership)
    q = select(Invitation).where(Invitation.tenant_id == tenant.id)
    # The owner-role mint ceiling only holds if a non-owner cannot reach such an
    # invite by reading either. Excluded in SQL rather than redacted afterwards,
    # so no filter combination can surface the row.
    if membership.role != TenantRole.owner:
        q = q.where(Invitation.role.is_distinct_from(TenantRole.owner.value))
    if kind:
        q = q.where(Invitation.kind == kind)
    if only_open:
        q = q.where(Invitation.used_at.is_(None), Invitation.expires_at > datetime.now(UTC))
    rows = (await db.execute(q.order_by(Invitation.created_at.desc()).limit(200))).scalars().all()
    bot_user = await _tenant_bot_username(db, tenant)
    return [_serialize(i, tenant.slug, bot_user) for i in rows]


@router.delete("/tenant/invitations/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invitation(
    invite_id: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    inv = (await db.execute(
        select(Invitation).where(
            Invitation.id == invite_id, Invitation.tenant_id == tenant.id,
        )
    )).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invitation_not_found")
    await db.delete(inv)
    await db.commit()


# ── public claim API (Mini-App or web) ────────────────────────────────

class InvitationContext(BaseModel):
    kind:           str
    tenant_name:    str
    tenant_slug:    str
    tenant_logo:    str | None
    role:           str | None
    preset:         dict
    custom_fields:  list[dict]
    locales:        list[str] = ["uz", "ru"]


@router.get(
    "/inv/{token}",
    response_model=InvitationContext,
    dependencies=[public_form_limit()],
)
async def get_invitation_context(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> InvitationContext:
    """Public — Mini-App calls this to render the right form.

    Rate-limited per IP: the route reveals whether a token is live, so an
    unthrottled GET is a token-enumeration oracle."""
    # (Batch 5) Exchange the raw credential for a scope before reading:
    # unlock() resolves it through rls_resolve_invitation and pins this ONE
    # invitation (plus its tenant, if it has one). Unknown credential → no
    # scope, and the query below finds nothing, which is the same 404 the
    # route already returns.
    await invite_codes.unlock(db, token)
    inv = (await db.execute(
        select(Invitation).where(invite_codes.lookup_filter(token))
    )).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invitation_not_found")
    if inv.used_at:
        raise HTTPException(status.HTTP_410_GONE, "invitation_already_used")
    if inv.expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, "invitation_expired")

    # Platform-admin invites have no tenant — render a platform-branded form.
    if inv.kind == "platform_admin" or inv.tenant_id is None:
        return InvitationContext(
            kind="platform_admin",
            tenant_name="Muddato",
            tenant_slug="platform",
            tenant_logo=None,
            role=inv.role,
            preset=inv.preset or {},
            custom_fields=[],
        )

    tenant = (await db.execute(select(Tenant).where(Tenant.id == inv.tenant_id))).scalar_one()
    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )).scalar_one_or_none()
    # The same resolver every other surface uses. This page had its own logo
    # precedence and showed the provisioning name — so an invitee could be
    # greeted by a shop name its owner had already changed.
    from app.services.tenant_profile import resolve as _resolve_profile

    profile = _resolve_profile(tenant, s)
    return InvitationContext(
        kind=inv.kind,
        tenant_name=profile.name,
        tenant_slug=profile.slug,
        tenant_logo=profile.logo_url,
        role=inv.role,
        preset=inv.preset or {},
        custom_fields=(s.customer_form_fields if (s and inv.kind == "customer") else []) or [],
    )


class InvitationIdentityIn(BaseModel):
    init_data: str


@router.post("/inv/{token}/identity", dependencies=[public_form_limit()])
async def invitation_identity(
    token: str,
    body: InvitationIdentityIn,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """What the server already knows about the person opening this invitation.

    The join form used to ask for a name and a phone unconditionally, including
    from people whose number Telegram had already proved and whose record the
    shop had already filled in. This is the answer it should have been asking
    for: everything established, decided server-side, so the form can render
    the remaining questions and nothing else.

    Deliberately narrow. It reports the CALLER's own verified number, masked,
    and a yes/no about the invitation they are holding. It never describes the
    invited customer's data, and it says nothing at all about any other tenant.
    """
    from app.core.phone_identity import PhoneEligibility, mask, same_number
    from app.core.telegram_auth import verify_init_data

    # (Batch 5) Exchange the raw credential for a scope before reading:
    # unlock() resolves it through rls_resolve_invitation and pins this ONE
    # invitation (plus its tenant, if it has one). Unknown credential → no
    # scope, and the query below finds nothing, which is the same 404 the
    # route already returns.
    await invite_codes.unlock(db, token)
    inv = (await db.execute(
        select(Invitation).where(invite_codes.lookup_filter(token))
    )).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invitation_not_found")
    if inv.used_at:
        raise HTTPException(status.HTTP_410_GONE, "invitation_already_used")
    if inv.expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, "invitation_expired")

    parsed = verify_init_data(
        body.init_data,
        bot_token=settings.telegram_bot_token,
        max_age_seconds=settings.telegram_initdata_max_age_seconds,
    )
    if parsed is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "init_data_invalid")

    # The caller's OWN phone-proof state, keyed on the Telegram id
    # `verify_init_data` just proved they control. Four scalars through a narrow
    # capability, not a row out of the user directory: this endpoint is
    # unauthenticated apart from that initData, so it must not be able to answer
    # "does account X exist" for an id the caller has not authenticated as.
    from app.core.identity_bootstrap import identity_state as _identity_state

    who = await _identity_state(db, parsed.user.id)

    verified = bool(who and who.verified)
    eligible = bool(who and who.eligibility == PhoneEligibility.eligible.value)
    state = "VERIFICATION_REQUIRED"
    if verified and who is not None:
        state = "VERIFIED" if eligible else (who.eligibility or "UNSUPPORTED_REGION")

    # Does the proved number belong to the customer this link was minted for?
    # A boolean, and only for the caller's own number — enough to render the
    # mismatch state, useless for probing anyone else's.
    matches: bool | None = None
    if verified and eligible and inv.customer_id and inv.tenant_id:
        target = (await db.execute(
            select(Customer).where(
                Customer.id == inv.customer_id, Customer.tenant_id == inv.tenant_id,
            )
        )).scalar_one_or_none()
        if target is not None:
            matches = same_number(target.phone, who.verified_phone if who else None)
            if not matches:
                state = "PHONE_DOES_NOT_MATCH_INVITATION"

    return {
        "state": state,
        "verified": verified,
        "phoneMasked": mask(who.verified_phone) if (verified and who) else None,
        "matchesInvitation": matches,
        "knownName": who.known_name if who else None,
        # The Telegram profile name, so a first-time joiner sees a sensible
        # default without the form claiming it is already stored.
        "telegramName": " ".join(
            p for p in (parsed.user.first_name, parsed.user.last_name) if p
        ).strip() or None,
    }


# Cap the free-form metadata bag: a public, unauthenticated endpoint must not
# let a caller persist an unbounded blob into the Customer's JSONB. Narrowly
# typed (str→str) and size-bounded so it can hold a handful of custom-field
# answers without becoming an arbitrary-payload sink.
_MAX_EXTRA_FIELDS = 32
_MAX_EXTRA_VALUE_LEN = 500


class ClaimCustomerIn(BaseModel):
    """Customer **and** guarantor (kafil) self-onboarding payload — they share
    the same passport/contact shape; guarantors additionally carry a
    ``relationship`` label."""
    init_data: str                      # Telegram Mini-App initData
    full_name: str = Field(min_length=2, max_length=120)
    #: Optional, because a person who has already PROVED a number must not be
    #: asked for it again — and because a typed value never becomes the proved
    #: one. Absent here and unproved elsewhere is refused by the handler, which
    #: is the only place that knows which of the two is true.
    phone:     UzPhoneOpt = Field(default=None, max_length=32)
    passport_series: str | None = Field(default=None, max_length=8)
    passport_number: str | None = Field(default=None, max_length=16)
    jshshir:   str | None = Field(default=None, min_length=14, max_length=14, pattern=r"^\d{14}$")
    birthdate: str | None = Field(default=None, max_length=32)
    address:   str | None = Field(default=None, max_length=512)
    relationship: str | None = Field(default=None, max_length=64)   # guarantor only
    locale:    Literal["uz", "ru"] = "uz"
    extra:     dict[str, str] = Field(default_factory=dict)

    @field_validator("extra")
    @classmethod
    def _bound_extra(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > _MAX_EXTRA_FIELDS:
            raise ValueError(f"at most {_MAX_EXTRA_FIELDS} extra fields allowed")
        for v in value.values():
            if len(v) > _MAX_EXTRA_VALUE_LEN:
                raise ValueError(f"extra value exceeds {_MAX_EXTRA_VALUE_LEN} chars")
        return value


class ClaimStaffIn(BaseModel):
    """Staff and platform-admin claim payload (both keyed off the Telegram
    account; no passport/customer fields).

    ``phone`` was REQUIRED here and was written straight onto the joining user's
    profile. That made an invitation link a way to type an account's phone
    number: whatever the invitee entered became `users.phone`, beside — and
    possibly contradicting — the number Telegram had proved for the same
    account. It is now optional and IGNORED, kept only so a client that still
    sends one is not 422'd mid-onboarding. The number that ends up on the
    account is the proved one, or none.
    """
    init_data:  str
    first_name: str = Field(min_length=1, max_length=64)
    last_name:  str | None = Field(default=None, max_length=64)
    phone:      UzPhoneOpt = Field(default=None, max_length=32)
    locale:     Literal["uz", "ru"] = "uz"


def _parse_claim_body(kind: str, body: dict) -> ClaimCustomerIn | ClaimStaffIn:
    """Validate the raw claim body against the model the invitation ``kind``
    dictates (a kind-discriminated union). Returns 422 on any schema violation,
    so the public endpoint never reads unvalidated ``body.get(...)`` fields."""
    model: type[ClaimCustomerIn] | type[ClaimStaffIn] = (
        ClaimStaffIn if kind in {"staff", "platform_admin"} else ClaimCustomerIn
    )
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.errors()) from exc


@router.post("/inv/{token}/claim", response_model=dict, dependencies=[public_form_limit()])
async def claim_invitation(
    token: str,
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Public — Mini-App posts the form here. Verifies initData → catches
    Telegram ID → upserts the user → creates Customer or TenantUser.

    The raw body is validated against ``ClaimCustomerIn`` / ``ClaimStaffIn``
    (selected by ``inv.kind``) before any field is read — the endpoint is
    public, unauthenticated and side-effectful, so an unvalidated dict here is
    an injection surface."""
    from app.core.telegram_auth import verify_init_data

    # (#180) Lock the token row so two concurrent claims serialize: the second
    # blocks until the first commits used_at, then sees it and is rejected. Without
    # the lock both read used_at=None and both materialize a Customer/TenantUser.
    # (Batch 5) Exchange the raw credential for a scope before reading:
    # unlock() resolves it through rls_resolve_invitation and pins this ONE
    # invitation (plus its tenant, if it has one). Unknown credential → no
    # scope, and the query below finds nothing, which is the same 404 the
    # route already returns.
    await invite_codes.unlock(db, token)
    inv = (await db.execute(
        select(Invitation)
        .where(invite_codes.lookup_filter(token))
        .with_for_update()
    )).scalar_one_or_none()
    if inv is None or inv.used_at:
        raise HTTPException(status.HTTP_410_GONE, "invitation_invalid_or_used")
    if inv.expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, "invitation_expired")

    # Bind the body to the right schema now that we know the invitation kind.
    claim = _parse_claim_body(inv.kind, body)

    # (MUDD-76) Deliberately NOT single-use-claimed like the login path: this
    # endpoint and the preview above are two steps of one join flow that share a
    # single launch's initData, and the claim itself is already one-shot —
    # ``used_at`` under the row lock taken a few lines up. The shortened window
    # is what applies here.
    parsed = verify_init_data(
        claim.init_data,
        bot_token=settings.telegram_bot_token,
        max_age_seconds=settings.telegram_initdata_max_age_seconds,
    )
    if parsed is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "init_data_invalid")
    u = parsed.user

    # Upsert User row. Telegram owns the name, @handle and avatar, so a
    # RETURNING joiner has them refreshed too — this branch used to create a
    # user from the verified initData and then never update it, so the profile
    # screen kept whatever Telegram had on the day they first joined while the
    # claim form asked them to type a name that went somewhere else entirely.
    #
    # (Batch 5 independent-review correction) This used to be a bare
    # `select(User)` plus `db.add(User(...))` under the plain tenant authority.
    # Both were wrong once the fail-closed policies landed: the SELECT could
    # only ever find a person already linked as a customer of the scoped tenant,
    # and `muddato_app` has no INSERT policy on `users` at all, so a first-time
    # joiner's claim was denied outright. It goes through the same
    # credential-derived bootstrap the login path uses — keyed on the Telegram
    # id `verify_init_data` has just proved — and then reads the row back under
    # the ordinary self policy, in THIS transaction (no commit), so the account
    # and the invitation being marked used still stand or fall together.
    from app.core.identity_bootstrap import load_self, upsert_telegram_account

    user_id = await upsert_telegram_account(
        db,
        telegram_id=u.id, username=u.username, first_name=u.first_name,
        last_name=u.last_name, photo_url=u.photo_url, locale=claim.locale,
    )
    user = await load_self(db, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "identity_unavailable")

    # A number this person PROVED they own outranks anything the form carries.
    # The form is a convenience for facts nobody has established yet; it is not
    # a way to overwrite one that has been.
    from app.core.phone_identity import PhoneEligibility
    from app.services.identity_link import LinkState, reconcile

    eligible_proof = (
        bool(user.verified_phone)
        and user.phone_eligibility == PhoneEligibility.eligible.value
    )
    identity_state = "VERIFICATION_REQUIRED"
    if user.verified_phone:
        identity_state = (
            "VERIFIED" if eligible_proof else (user.phone_eligibility or "UNSUPPORTED_REGION")
        )

    # A customer or guarantor relationship carries contracts and payment history,
    # so it is created ONLY against proved ownership. A typed number is a claim
    # about somebody; it is not a claim BY them, and an invitation link can be
    # forwarded to anyone. Nothing is bound, nothing changes status and the token
    # stays live until the proof exists.
    #
    # Staff and platform invitations ask a NARROWER question. Who authorises the
    # membership is settled by the person who minted the link, so the customer
    # eligibility rule (an Uzbek number, because only such a number can be a
    # customer of this market) does not apply — an operator or a shop manager on
    # a foreign number is a real person doing a real job. What DOES apply is
    # ownership: the account must have proved a number belongs to it, because
    # that number is now the account's identity and the claim body no longer
    # supplies one. Ownership without market eligibility is exactly the
    # distinction `phone_identity.classify` keeps apart, and this is the case it
    # was kept apart for.
    if inv.kind in {"customer", "guarantor"} and not eligible_proof:
        return {"ok": False, "kind": inv.kind, "identity_state": identity_state}

    if inv.kind == "customer":
        assert isinstance(claim, ClaimCustomerIn)
        # Only a `platform_admin` invitation leaves `tenant_id` NULL; a customer
        # one always names the tenant it onboards into.
        assert inv.tenant_id is not None
        # If the invite was minted for a pre-created customer (operator filled all
        # data, then handed over a one-time link), attach the joiner's Telegram id
        # to THAT customer instead of creating a new one — that flips them to
        # "joined" so contracts can be issued and reminders delivered.
        c = None
        if inv.customer_id:
            c = (await db.execute(
                select(Customer).where(
                    Customer.id == inv.customer_id, Customer.tenant_id == inv.tenant_id,
                )
            )).scalar_one_or_none()
        if c is not None:
            # One transaction decides token, tenant, expected customer and proved
            # identity together. A mismatch is reported as a state, never resolved
            # by attaching whoever the number does match.
            link = await reconcile(
                db, user=user, tenant_id=inv.tenant_id, expected_customer_id=c.id,
            )
            if not link.ok:
                # The invitation is left UNUSED and nothing is bound, so the
                # person can finish once the mismatch is resolved — and so a
                # forwarded link buys its holder no access in the meantime.
                return {
                    "ok": False,
                    "kind": inv.kind,
                    "identity_state": (
                        "PHONE_DOES_NOT_MATCH_INVITATION"
                        if link.state is LinkState.no_match else link.state.value
                    ),
                }
            identity_state = "VERIFIED"
        else:
            # Legacy / unbound self-onboarding: create a new Customer from the form.
            # Resolve auto-approve flag for this tenant — if the owner has
            # turned off auto-approve in Settings → Customer Form, the new
            # Customer lands in ``pending`` and needs an explicit approve from
            # the dashboard before any installment can be issued against them.
            ts = (await db.execute(
                select(TenantSettings).where(TenantSettings.tenant_id == inv.tenant_id)
            )).scalar_one_or_none()
            auto_approve = ts.auto_approve_customers if ts else False
            initial_status = CustomerStatus.active if auto_approve else CustomerStatus.pending

            # Build Customer; use phone from body (Telegram doesn't share it freely).
            c = Customer(
                tenant_id=inv.tenant_id,
                full_name=claim.full_name or f"{u.first_name or ''} {u.last_name or ''}".strip(),
                # The proved number, not the typed one. Reaching here requires a
                # proof, so a typed value could only ever disagree with it.
                phone=user.verified_phone,
                telegram_id=u.id,
                passport_series=claim.passport_series,
                passport_number=claim.passport_number,
                jshshir=claim.jshshir,
                address=claim.address,
                status=initial_status,
                metadata_json={"extra": claim.extra, "via_invitation": str(inv.id)},
            )
            if claim.passport_series and claim.passport_number:
                c.passport_id = f"{claim.passport_series.upper()}{claim.passport_number}"
            if claim.birthdate:
                from contextlib import suppress
                from datetime import date as _date
                with suppress(ValueError, TypeError):
                    c.birthdate = _date.fromisoformat(claim.birthdate)
            db.add(c)
    elif inv.kind == "guarantor":
        assert isinstance(claim, ClaimCustomerIn)
        # Self-onboarding guarantor (kafil). Upsert by (tenant, phone); if
        # the invite preset names an installment, link them to it.
        # A guarantor stands behind somebody else's debt; the number on that
        # undertaking is the proved one.
        phone = user.verified_phone
        existing_g = None
        if phone:
            existing_g = (await db.execute(
                select(Guarantor).where(
                    Guarantor.tenant_id == inv.tenant_id, Guarantor.phone == phone,
                )
            )).scalar_one_or_none()
        g = existing_g or Guarantor(tenant_id=inv.tenant_id, phone=phone or "")
        g.full_name = claim.full_name or g.full_name or (
            f"{u.first_name or ''} {u.last_name or ''}".strip()
        )
        g.telegram_id = u.id
        if claim.passport_series:
            g.passport_series = claim.passport_series
        if claim.passport_number:
            g.passport_number = claim.passport_number
        if claim.jshshir:
            g.jshshir = claim.jshshir
        if claim.relationship:
            g.relationship_label = claim.relationship
        if claim.address:
            g.address = claim.address
        if existing_g is None:
            db.add(g)
        await db.flush()
        ins_id = (inv.preset or {}).get("installment_id")
        if ins_id:
            try:
                ins_uuid = UUID(str(ins_id))
            except (ValueError, TypeError):
                ins_uuid = None
            if ins_uuid is not None:
                ins = (await db.execute(
                    select(Installment).where(
                        Installment.id == ins_uuid, Installment.tenant_id == inv.tenant_id,
                    )
                )).scalar_one_or_none()
                if ins is not None:
                    already = (await db.execute(
                        select(InstallmentGuarantor).where(
                            InstallmentGuarantor.installment_id == ins.id,
                            InstallmentGuarantor.guarantor_id == g.id,
                        )
                    )).scalar_one_or_none()
                    if already is None:
                        seq = (await db.execute(
                            select(func.count(InstallmentGuarantor.id)).where(
                                InstallmentGuarantor.installment_id == ins.id,
                            )
                        )).scalar_one() + 1
                        db.add(InstallmentGuarantor(
                            installment_id=ins.id, guarantor_id=g.id, sequence=seq,
                        ))
    elif inv.kind == "staff":
        assert isinstance(claim, ClaimStaffIn)
        assert inv.tenant_id is not None  # staff invites are always tenant-scoped
        role = TenantRole(inv.role or "sales")
        existing = (await db.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == inv.tenant_id, TenantUser.user_id == user.id,
            )
        )).scalar_one_or_none()
        if existing is not None:
            # (sec) A staff invitation onboards a new member only. Changing an
            # existing membership's role, or reactivating a revoked one, is an
            # owner-side staff-management operation (ROLE-011/012) and must never
            # be a side effect of holding a link. Rejected before any write and
            # before `used_at` is stamped, so the membership is untouched and the
            # token stays valid for its intended recipient.
            raise HTTPException(status.HTTP_409_CONFLICT, "already_a_member")
        # Ownership, checked here rather than beside the customer gate above so
        # it cannot mask a STRUCTURAL refusal: "you are already a member" and
        # "this invitation was already used" are true regardless of anyone's
        # phone, and answering them with "verify your number first" would send a
        # person off to prove something that changes nothing.
        #
        # Market eligibility is deliberately NOT required (see the note beside
        # the customer gate): an employee on a foreign number is a real person.
        if not user.verified_phone:
            return {"ok": False, "kind": inv.kind, "identity_state": identity_state}
        # (#135) New staff is a net +1 active member — enforce the plan's max_staff
        # atomically. locked_staff_decision locks the tenant row so two concurrent
        # claims can't both pass the check and exceed the cap.
        from app.services import plan_limits
        decision = await plan_limits.locked_staff_decision(db, inv.tenant_id)
        if not decision.allowed:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                f"plan_staff_limit_reached:{decision.limit}",
            )
        db.add(TenantUser(
            tenant_id=inv.tenant_id, user_id=user.id, role=role, is_active=True,
            invited_by_id=inv.created_by_user_id,
            invitation_status="active",   # they just claimed it
        ))
        # `claim.phone` is deliberately not written. The account's number is the
        # one proved against this Telegram identity; a value carried in the
        # claim body is a client assertion about identity and proves nothing.
        if claim.first_name:
            user.first_name = claim.first_name
        if claim.last_name:
            user.last_name = claim.last_name
    elif inv.kind == "platform_admin":
        assert isinstance(claim, ClaimStaffIn)
        # The account about to receive a platform role must have proved which
        # number it belongs to. This branch used to take the number from the
        # claim body instead — a client assertion about identity, accepted in
        # the same request that grants the privilege.
        if not user.verified_phone:
            return {"ok": False, "kind": inv.kind, "identity_state": identity_state}
        from app.models.enums import PlatformRole
        role_value = inv.role if inv.role in {"super_admin", "support"} else "support"
        user.platform_role = PlatformRole(role_value)
        user.is_active = True
        if claim.first_name:
            user.first_name = claim.first_name
        # Same rule, and it matters more here: this branch grants a PLATFORM
        # role. Nothing about the identity being elevated may come from the
        # claim body.
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown_invitation_kind")

    inv.used_at = datetime.now(UTC)
    inv.used_by_user_id = user.id
    from app.services import audit
    await audit.record(
        db, action="invitation.claim", tenant_id=inv.tenant_id,
        actor_user_id=user.id, entity_type="invitation",
        entity_id=str(inv.id),
        after={"kind": inv.kind, "telegram_id": u.id},
    )
    if inv.kind == "platform_admin":
        # (#130) The privilege grant itself — a distinct, filterable security event
        # (a platform super_admin/support role was just granted), recorded with the
        # role so it isn't buried among generic customer/staff claims.
        await audit.record(
            db, action="admin_invitation.claim", tenant_id=None,
            actor_user_id=user.id, entity_type="user", entity_id=str(user.id),
            after={
                "platform_role": user.platform_role.value if user.platform_role else None,
                "invitation_id": str(inv.id),
            },
        )
    await db.commit()

    # TODO Round 18: notify_tenant_target so the tenant's bot pings the owner
    # ("Yangi mijoz qo'shildi: Ali Valiyev (+998…)").

    # Issue a session token so the Mini-App can log the joiner straight into
    # their dashboard — no second login, no "link expired" on reload.
    from app.core.security import issue_token
    return {
        "ok": True,
        "kind": inv.kind,
        "role": inv.role,
        "telegram_id": u.id,
        "user_id": str(user.id),
        # What the SERVER concluded about this person's phone. The client renders
        # the matching state; it never decides one.
        "identity_state": identity_state,
        "token": issue_token(user_id=str(user.id)),
    }
