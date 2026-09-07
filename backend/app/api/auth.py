"""Telegram-based authentication."""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import AfterValidator, BaseModel, Field
from pydantic.networks import validate_email
from pydantic_core import PydanticCustomError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.identity_bootstrap import (
    MAX_BOT_TOKEN_CANDIDATES as _MAX_TENANT_BOT_TOKEN_CHECKS,
)
from app.core.identity_bootstrap import (
    bot_token_candidates,
    load_self,
    upsert_telegram_account,
)
from app.core.initdata_replay import ReplayStoreUnavailable, claim_initdata
from app.core.phone_identity import account_phone, same_number
from app.core.ratelimit import login_limit, provision_trial_limit, signup_limit
from app.core.scope import self_serve_provisioning_gate
from app.core.security import current_user, issue_token
from app.core.telegram_auth import (
    TelegramLoginPayload,
    WebAppInitData,
    verify_init_data,
    verify_login,
    verify_oauth_result,
)
from app.core.validators import UzPhoneOpt
from app.db import get_db
from app.models import TenantSettings, TenantUser, User
from app.schemas.auth import LoginResponse, MeResponse, MeTenant
from app.services.display_names import MAX_DISPLAY_NAME, normalize_display_name, person_name

#: NO router-level authority. (Batch 5 independent-review correction.)
#:
#: This router used to carry ``Depends(identity_authority)``, and that authority
#: held ``USING (true) WITH CHECK (true)`` on ``users``. So every handler here —
#: including the authenticated ones, and including any added later — ran with
#: read/write access to the entire user directory before a credential had been
#: checked. A new unauthenticated endpoint under ``/auth`` inherited it silently.
#:
#: The identity authority is now taken per OPERATION, for one statement, by the
#: three functions in ``app.core.identity_bootstrap`` — and that authority no
#: longer has any policy on ``users``, so even holding it grants no directory
#: access; only the narrow capability functions do. Everything after credential
#: verification is an ordinary signed-in request under ``muddato_app``, reading
#: and writing its own row through ``app.user_id``.
router = APIRouter(prefix="/auth", tags=["auth"])

log = logging.getLogger("muddato.auth")


def apply_telegram_identity(
    user: User,
    *,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    photo_url: str | None,
) -> None:
    """Refresh the fields TELEGRAM owns on an existing user.

    Telegram is the source of truth for a person's name, @handle and avatar —
    they are edited in Telegram, not here, and the profile screen displays them.
    Anything the shop owns (phone, birthdate, the Customer record's own
    `full_name`) is deliberately untouched.

    Only non-empty values overwrite: Telegram omits `last_name` for users who
    have none, and letting an absent field blank a stored one would lose data on
    every login.

    Shared with the invitation claim, which used to create a user from these
    fields and then never update them again — so a returning joiner's profile
    kept whatever name Telegram had on the day they first opened the app.
    """
    if username:
        user.telegram_username = username
    if first_name:
        user.first_name = first_name
    if last_name:
        user.last_name = last_name
    if photo_url:
        user.photo_url = photo_url


async def _upsert_user(
    db: AsyncSession,
    *,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    photo_url: str | None,
    language_code: str | None = None,
) -> User:
    """Find-or-create the account for a Telegram id the caller has PROVED.

    Every call site verifies first — a Login-Widget HMAC, an OAuth HMAC, an
    id_token checked against Telegram's JWKS, or Mini-App initData. This is the
    write that verification has earned, and it is the only reason the identity
    authority is ever taken.

    Two boundaries live in these few lines:

    * the write goes through ``upsert_telegram_account``, whose SQL signature
      accepts the Telegram display fields and NOTHING else — ``platform_role``,
      ``is_active`` and the phone-proof columns cannot be set through this path
      however the request body is shaped;
    * ``load_self`` then pins ``app.user_id`` and reads the row back under the
      ordinary ``muddato_app`` self policy. Past that line this is a normal
      signed-in request, not a bootstrap.
    """
    user_id = await upsert_telegram_account(
        db,
        telegram_id=telegram_id, username=username, first_name=first_name,
        last_name=last_name, photo_url=photo_url,
        locale=("ru" if (language_code or "").startswith("ru") else "uz"),
    )
    await db.commit()
    user = await load_self(db, user_id)
    if user is None:
        # Reachable, despite appearances: `upsert_telegram_account` returns None
        # for a falsy Telegram id rather than minting an account for one, and a
        # row can be deleted between the upsert and the read. Either way the
        # caller is not signed in, and 401 is the honest answer.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "identity_unavailable")
    return user


@router.post("/telegram", response_model=LoginResponse, dependencies=[login_limit()])
async def telegram_login(
    payload: TelegramLoginPayload,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    """Login Widget (browser) — verifies the legacy HMAC scheme."""
    if not verify_login(payload, bot_token=settings.telegram_bot_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "telegram_hmac_failed")

    user = await _upsert_user(
        db,
        telegram_id=payload.id, username=payload.username,
        first_name=payload.first_name, last_name=payload.last_name,
        photo_url=payload.photo_url,
    )
    token = issue_token(user_id=str(user.id))
    # (#P1-A / #257) Set HttpOnly session cookie + readable double-submit CSRF cookie
    from app.core.security import establish_session
    establish_session(response, token)
    return LoginResponse(token=token)


class WebAppLoginRequest(BaseModel):
    init_data: str


class OAuthCallbackRequest(BaseModel):
    """`tgAuthResult` payload decoded by the frontend callback page."""
    id: int
    auth_date: int
    hash: str = Field(min_length=32, max_length=2048)   # JWTs are long
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    language_code: str | None = None
    phone_number: str | None = None


def _looks_like_jwt(s: str) -> bool:
    return s.count(".") == 2 and len(s) > 60


async def _oidc_claims_from_hash(raw_hash: str) -> dict:
    """Verify an OIDC id_token passed in the legacy ``hash`` field and return its claims.

    Telegram signs id_tokens with ASYMMETRIC keys (RS256 default) verified via the
    JWKS endpoint — NOT HS256 over the client_secret. Delegates to the canonical
    verifier in ``auth_oidc`` (fetches+caches JWKS, matches by ``kid``, checks
    issuer + audience + expiry). Raises 503 if client_id is unset, 401 on an invalid
    token.
    """
    from jwt import InvalidTokenError

    # Lazy import: auth_oidc imports _upsert_user from this module, so a top-level
    # import here would be circular.
    from app.api.auth_oidc import _verify_id_token

    if not settings.telegram_login_client_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "oidc_client_id_unset")
    try:
        return await _verify_id_token(raw_hash)
    except HTTPException:
        raise  # unresolvable signing key — already a clean 401/502
    except InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "telegram_oidc_invalid") from e


def _apply_oidc_claims(payload: OAuthCallbackRequest, claims: dict) -> None:
    """Overwrite the payload's identity fields with the verified id_token claims.

    Telegram's claim set is sub / name / preferred_username / picture / phone_number
    (no given_name / family_name / locale); fall back to whatever the client sent for
    anything Telegram omits.
    """
    payload.id = int(claims.get("sub", payload.id))
    payload.first_name = claims.get("name") or claims.get("given_name") or payload.first_name
    payload.last_name = claims.get("family_name") or payload.last_name
    payload.username = claims.get("preferred_username") or payload.username
    payload.photo_url = claims.get("picture") or payload.photo_url
    payload.phone_number = claims.get("phone_number") or payload.phone_number


@router.post("/telegram/oauth", response_model=LoginResponse, dependencies=[login_limit()])
async def telegram_oauth(
    payload: OAuthCallbackRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    """OAuth 2.0 / Login Widget flow.

    Accepts three shapes:
      • Legacy Login Widget — {id, first_name, ..., hash}; verify HMAC of bot_token
      • OAuth widget JSON   — same shape; HMAC of client_secret as fallback
      • OIDC id_token       — `hash` holds the JWT; verify against Telegram's JWKS (RS256)
    """
    data = payload.model_dump(exclude_none=True)
    raw_hash = payload.hash

    verified = False
    if _looks_like_jwt(raw_hash):
        # OIDC id_token in `hash` — verify against Telegram's JWKS, then trust its
        # canonical claims over whatever the client sent.
        _apply_oidc_claims(payload, await _oidc_claims_from_hash(raw_hash))
        verified = True
    else:
        verified = verify_oauth_result(
            data,
            bot_token=settings.telegram_bot_token,
            client_secret=settings.telegram_login_client_secret,
        )

    if not verified:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "telegram_oauth_hmac_failed")

    user = await _upsert_user(
        db,
        telegram_id=payload.id, username=payload.username,
        first_name=payload.first_name, last_name=payload.last_name,
        photo_url=payload.photo_url, language_code=payload.language_code,
    )
    return LoginResponse(token=issue_token(user_id=str(user.id)))


class SignupRequest(OAuthCallbackRequest):
    """Self-service signup — an OAuth callback payload plus the business name.

    **Disabled**: self-serve provisioning is LATER (``app/core/scope.py``), so the
    gate below answers before any of the body below runs. Retained for V2.
    """
    business_name: str = Field(min_length=2, max_length=120)
    locale: str = Field(default="uz", pattern="^(uz|ru)$")


class SignupResponse(LoginResponse):
    tenant_slug: str
    created: bool


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    # Gate first, limiter second. Two reasons, both about the disabled state:
    # the refusal must not depend on Redis (this bucket is fail-closed, so a
    # limiter-first order would answer 503 during an outage and 403 otherwise —
    # not one stable status), and refusing before the Redis round-trip makes a
    # flood against a dead route free to serve. The limiter declaration is
    # retained unchanged, so flipping the scope flag restores full throttling.
    dependencies=[self_serve_provisioning_gate(), signup_limit()],
)
async def signup(
    payload: SignupRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SignupResponse:
    """Telegram OAuth → user → trial tenant (owner = caller). Unreachable: see gate."""
    data = payload.model_dump(exclude_none=True, exclude={"business_name", "locale"})
    raw_hash = payload.hash

    verified = False
    if _looks_like_jwt(raw_hash):
        _apply_oidc_claims(payload, await _oidc_claims_from_hash(raw_hash))
        verified = True
    else:
        verified = verify_oauth_result(
            data,
            bot_token=settings.telegram_bot_token,
            client_secret=settings.telegram_login_client_secret,
        )

    if not verified:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "telegram_oauth_hmac_failed")

    user = await _upsert_user(
        db,
        telegram_id=payload.id, username=payload.username,
        first_name=payload.first_name, last_name=payload.last_name,
        photo_url=payload.photo_url, language_code=payload.language_code,
    )

    # Idempotent: reuse an existing owned tenant rather than provisioning a 2nd.
    from app.models.enums import TenantRole
    existing = (await db.execute(
        select(TenantUser).where(
            TenantUser.user_id == user.id,
            TenantUser.role == TenantRole.owner,
            TenantUser.is_active.is_(True),
        )
    )).scalars().first()
    if existing is not None:
        tenant = await existing.awaitable_attrs.tenant
        return SignupResponse(
            token=issue_token(user_id=str(user.id)),
            tenant_slug=tenant.slug, created=False,
        )

    from app.services.onboarding import provision_tenant_from_intent
    # (#209) A platform operator must not self-provision a customer tenant.
    if user.platform_role is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "platform_admin_cannot_create_tenant")
    tenant = await provision_tenant_from_intent(
        db, user=user,
        intent={
            "business_name": payload.business_name,
            "plan_code": "trial",
            "locale": payload.locale,
            "phone": payload.phone_number,
        },
    )
    return SignupResponse(
        token=issue_token(user_id=str(user.id)),
        tenant_slug=tenant.slug, created=True,
    )


class ProvisionTrialRequest(BaseModel):
    """Authenticated self-service: turn a logged-in user into a tenant owner.

    **Disabled**: self-serve provisioning is LATER (``app/core/scope.py``), so the
    gate below answers before the caller is even authenticated. Retained for V2.
    """
    business_name: str = Field(min_length=2, max_length=120)
    owner_name: str | None = Field(default=None, max_length=120)
    phone: UzPhoneOpt = Field(default=None, max_length=32)
    # The plan the owner is interested in (stored for sales follow-up). The
    # trial itself is always a 14-day trial regardless.
    # Stored as the owner's interested plan (real catalog code from /plans/public).
    # Loose validation so a new/renamed plan never 422s the wizard.
    plan_code: str | None = Field(default=None, max_length=40)
    locale: str = Field(default="uz", pattern="^(uz|ru)$")
    # Onboarding wizard — segment + acquisition attribution.
    business_type: str | None = Field(default=None, max_length=48)
    business_type_other: str | None = Field(default=None, max_length=120)
    how_found: str | None = Field(default=None, max_length=48)
    how_found_other: str | None = Field(default=None, max_length=120)


class ProvisionTrialResponse(BaseModel):
    tenant_slug: str
    created: bool


@router.post(
    "/provision-trial",
    response_model=ProvisionTrialResponse,
    # Gate first (see /signup), and it also precedes `current_user`, so a call to
    # a disabled route authenticates nobody, parses nothing and writes nothing.
    dependencies=[self_serve_provisioning_gate(), provision_trial_limit()],
)
async def provision_trial(
    payload: ProvisionTrialRequest,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProvisionTrialResponse:
    """Provision a trial tenant for the current user. Unreachable: see gate."""
    from app.models.enums import TenantRole

    # Capture the owner's name on their profile (don't overwrite existing).
    #
    # `payload.phone` is NOT written to the user any more. It is the SHOP's
    # public contact number — the one printed on contracts and shown to that
    # shop's customers — and it flows to `Tenant.contact_phone` below, which is
    # a different attribute of a different entity. Copying it onto the person
    # created the account's second phone: an unproved number, typed during
    # signup, sitting beside the Telegram-proved one and contradicting it.
    if payload.owner_name and not user.first_name:
        user.first_name = payload.owner_name.strip()

    existing = (await db.execute(
        select(TenantUser).where(
            TenantUser.user_id == user.id,
            TenantUser.role == TenantRole.owner,
            TenantUser.is_active.is_(True),
        )
    )).scalars().first()
    if existing is not None:
        await db.commit()
        tenant = await existing.awaitable_attrs.tenant
        return ProvisionTrialResponse(tenant_slug=tenant.slug, created=False)

    from app.services.onboarding import provision_tenant_from_intent
    # (#209) A platform operator must not self-provision a customer tenant.
    if user.platform_role is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "platform_admin_cannot_create_tenant")
    tenant = await provision_tenant_from_intent(
        db, user=user,
        intent={
            "business_name": payload.business_name,
            "plan_code": "trial",
            "locale": payload.locale,
            "interested_plan": payload.plan_code,
            "contact_phone": payload.phone,
            "business_type": payload.business_type,
            "business_type_other": payload.business_type_other,
            "how_found": payload.how_found,
            "how_found_other": payload.how_found_other,
        },
    )
    await db.commit()
    return ProvisionTrialResponse(tenant_slug=tenant.slug, created=True)


async def _verify_webapp_initdata(db: AsyncSession, init_data: str) -> WebAppInitData | None:
    """Verify Mini-App initData against the platform bot token, then fall back to
    any configured per-tenant bot token (#59).

    A tenant's own @BotFather bot (tenant_settings.bot_token) opens the same WebApp
    but Telegram signs the initData HMAC with the TENANT's token. Checking only the
    platform token would 401 every tenant-bot user ('telegram_initdata_invalid' →
    the 'something went wrong' card). The fallback is reached only when the platform
    token doesn't match (a genuine tenant-bot login) and is bounded by
    ``MAX_BOT_TOKEN_CANDIDATES`` so a forged initData can't cause an unbounded
    crypto scan.

    The candidate list now comes from the identity capability rather than a
    direct cross-tenant ``tenant_settings`` read. That read was not merely
    over-broad, it was DEAD: under RLS the identity policy on ``tenant_settings``
    keys on ``app.user_id``, which is unset before login by definition, so it
    silently returned zero tokens and every tenant-bot login failed.
    """
    max_age = settings.telegram_initdata_max_age_seconds
    parsed = verify_init_data(init_data, bot_token=settings.telegram_bot_token, max_age_seconds=max_age)
    if parsed is not None:
        return parsed
    for token in await bot_token_candidates(db, _MAX_TENANT_BOT_TOKEN_CHECKS):
        if (parsed := verify_init_data(init_data, bot_token=token, max_age_seconds=max_age)) is not None:
            return parsed
    return None


@router.post("/telegram/webapp", response_model=LoginResponse, dependencies=[login_limit()])
async def telegram_webapp_login(
    body: WebAppLoginRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    """Mini-App initData → session.

    Verifies the WebApp HMAC against the platform or the originating tenant's
    bot token (#59), then spends the initData: each signed string buys exactly
    one session (MUDD-76).
    """
    parsed = await _verify_webapp_initdata(db, body.init_data)
    if parsed is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "telegram_initdata_invalid")

    # (MUDD-76) A valid signature is not a fresh one. The same signed string
    # stays verifiable for the whole freshness window, so whoever captures it —
    # shared device, a log line, a referrer escaping a tenant Mini-App — could
    # log in as its owner until it aged out. Spend it: first presentation wins,
    # every replay inside the window is refused.
    #
    # This runs AFTER the HMAC check on purpose. Claiming first would let forged
    # initData write arbitrary keys into the store, and would burn the victim's
    # real credential if an attacker guessed it.
    try:
        first_use = await claim_initdata(
            parsed.hash,
            purpose="webapp_login",
            ttl_seconds=settings.telegram_initdata_max_age_seconds,
        )
    except ReplayStoreUnavailable as e:
        # Fail CLOSED, like the OIDC state store. Falling back to "allow" would
        # hand back the 24h replay window to anyone able to degrade Redis, which
        # is precisely the failure this guard exists to remove.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "auth_store_unavailable"
        ) from e
    if not first_use:
        # Distinct from ``telegram_initdata_invalid`` so the client can tell
        # "this launch already logged in" (keep the session it holds) from
        # "these credentials are bad" (drop it). The detail leaks nothing: the
        # caller supplied the initData being refused.
        log.warning("auth.webapp_initdata_replayed telegram_id=%s", parsed.user.id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "telegram_initdata_replayed")

    u = parsed.user
    user = await _upsert_user(
        db,
        telegram_id=u.id, username=u.username,
        first_name=u.first_name, last_name=u.last_name,
        photo_url=u.photo_url, language_code=u.language_code,
    )
    token = issue_token(user_id=str(user.id))
    # (#P1-A / #257) Set HttpOnly session cookie + readable double-submit CSRF cookie
    from app.core.security import establish_session
    establish_session(response, token)
    return LoginResponse(token=token)



@router.delete("/logout", status_code=status.HTTP_200_OK)
async def logout(
    user: Annotated[User, Depends(current_user)],
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, str]:
    """(#734) Logout — revoke all outstanding tokens and clear the session cookie.
    
    Returns 200 + clears the muddato.session cookie so the browser doesn't resend it.
    The revocation epoch persists — old tokens remain invalid until re-auth.
    """
    # Revoke all outstanding tokens by bumping the revocation epoch
    user.tokens_valid_from = datetime.now(UTC)
    await db.commit()
    
    # Clear the session cookie (value='', max_age=0 tells the browser to delete it)
    # and the double-submit CSRF cookie alongside it (MUDD-57): the two are set
    # together by ``establish_session``, so leaving one behind means a logged-out
    # browser still carries a readable ``muddato.csrf`` that the next visitor to
    # the machine inherits.
    from app.core.security import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
    for name in (SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
        response.delete_cookie(key=name, path="/", domain=None)
    
    return {"message": "logged out"}

@router.get("/debug", tags=["auth"])
async def auth_debug(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Inspect what the bearer token decodes to — for diagnosing 401 storms.

    (#131) Disabled in production: it's unauthenticated and echoes decoded JWT
    claims + user PII (telegram_id, platform_role, token prefix), a recon/info-leak
    surface. Available only in local/staging.
    """
    if settings.is_production:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    from app.core.security import decode_token
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return {"ok": False, "error": "no_bearer", "header": auth[:40]}
    token = auth.split(" ", 1)[1]
    try:
        payload = decode_token(token)
    except HTTPException as e:
        return {"ok": False, "error": "decode_failed", "detail": e.detail, "token_prefix": token[:20]}
    user_id = payload.get("sub")
    # `load_self`, not a bare SELECT: it pins `app.user_id` to the subject of the
    # signature this endpoint just verified and reads through the ordinary self
    # policy. A plain `select(User).where(User.id == <anything>)` here would be a
    # pre-auth arbitrary user lookup — the exact shape this correction removes,
    # and the one `tests/test_batch5_mutation_proofs.py` reintroduces to prove
    # the guard bites.
    user = await load_self(db, user_id)
    return {
        "ok": True,
        "token_claims": payload,
        "user_id_from_token": user_id,
        "user_found": user is not None,
        "user_is_active": user.is_active if user else None,
        "telegram_id": user.telegram_id if user else None,
        "platform_role": user.platform_role.value if user and user.platform_role else None,
        "tenants_count": len(list(await user.awaitable_attrs.memberships)) if user else 0,
    }


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """(#123) Revoke every outstanding session token for the caller, including this
    one — bumps the user's revocation epoch so all tokens with an earlier ``iat`` are
    rejected. The client must re-authenticate."""
    user.tokens_valid_from = datetime.now(UTC)
    await db.commit()


@router.get("/me", response_model=MeResponse, response_model_by_alias=True)
async def me(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MeResponse:
    # (F-20) Eager-load each membership's tenant in one batched IN query instead of
    # a lazy per-row fetch — a user in N tenants was N+1 SELECTs. selectinload is the
    # convention for any list/loop that reads a relationship (joinedload would
    # cartesian-product across collections; lazy awaitable_attrs N+1s in a loop).
    rows = (
        await db.execute(
            select(TenantUser)
            .where(TenantUser.user_id == user.id, TenantUser.is_active.is_(True))
            .options(selectinload(TenantUser.tenant))
        )
    ).scalars().all()

    # Each shop's own currency, so every screen labels money the way that shop's
    # generated contracts and receipts do. Fetched in ONE query keyed by tenant
    # id rather than per membership — this endpoint is on the boot path of every
    # client, and an N+1 here is paid on every cold start.
    #
    # (Batch 5 independent-review correction) The old note here said this read
    # was sound because "the transaction still runs as the connection role and
    # is not subject to RLS". That has not been true since Batch 5 and is the
    # opposite of the posture now: the connection is a restricted login that
    # inherits nothing, and this transaction runs as `muddato_app`.
    #
    # What makes the read sound is a policy, not an exemption:
    # `rls_tenant_settings_app_self` allows SELECT on the settings of tenants
    # the signed-in user is a member of, which is exactly the set below —
    # `tenant_ids` comes from this user's own memberships. A row for a shop they
    # do not belong to is filtered by the database, not trusted to this query.
    #
    # It stays one query keyed by tenant id rather than one per membership:
    # this endpoint is on every client's boot path, and an N+1 here is paid on
    # every cold start.
    tenant_ids = [m.tenant_id for m in rows]
    currency_by_tenant: dict[UUID, str] = {}
    if tenant_ids:
        currency_by_tenant = {
            tid: cur
            for tid, cur in (
                await db.execute(
                    select(TenantSettings.tenant_id, TenantSettings.currency)
                    .where(TenantSettings.tenant_id.in_(tenant_ids))
                )
            ).all()
        }

    tenants: list[MeTenant] = []
    for membership in rows:
        tenant = await membership.awaitable_attrs.tenant
        if tenant is None:
            continue
        tenants.append(
            MeTenant(
                id=tenant.id, slug=tenant.slug, name=tenant.name,
                role=membership.role.value, status=tenant.status.value, plan_code=tenant.plan_code,
                trial_ends_at=tenant.trial_ends_at.isoformat() if tenant.trial_ends_at else None,
                # A shop with no settings row yet has not chosen one.
                currency=currency_by_tenant.get(tenant.id) or "UZS",
            )
        )

    return MeResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        telegram_username=user.telegram_username,
        first_name=user.first_name,
        last_name=user.last_name,
        display_name=person_name(user),
        photo_url=user.photo_url,
        # THE account phone — the proved one, never the typed one. This served
        # `user.phone` while the Profile screen also rendered the verified
        # number beside it, so the product showed two phone numbers for one
        # person and invited them to keep a second one up to date.
        phone=account_phone(user),
        email=user.email,
        birthdate=user.birthdate.isoformat() if user.birthdate else None,
        gender=user.gender,
        locale=user.locale,
        platform_role=user.platform_role.value if user.platform_role else None,
        tenants=tenants,
    )


def _optional_email(v: str | None) -> str | None:
    """(LOW-3) Validate an optional, clearable email address.

    Plain ``str`` let anything through to ``users.email``, which carries a UNIQUE
    constraint (r72) — so a typo did not bounce at the edge, it took a slot in the
    identity space and only surfaced when the real owner tried to register it.

    Not ``EmailStr`` directly: ``update_me`` treats ``""`` as "clear my email", and
    ``EmailStr`` rejects the empty string, which would have removed the only way to
    unset the field. Blank normalises to ``None`` here so that stays possible, and
    everything else must be a real address.
    """
    if v is None:
        return None
    s = v.strip()
    if not s:
        return None
    try:
        return validate_email(s)[1]
    except PydanticCustomError as e:
        raise ValueError("invalid_email") from e


#: An email that may be absent or explicitly cleared, but is never junk.
EmailOpt = Annotated[str | None, AfterValidator(_optional_email)]


class ProfileUpdateIn(BaseModel):
    """What a person may change about themselves.

    `first_name` / `last_name` are absent on purpose: Telegram owns them and
    refreshes them from validated initData on every sign-in, so a value typed
    here would be silently reverted — which is exactly how a saved name appeared
    to vanish. The self-chosen name has its own column and its own precedence.
    """

    display_name: str | None = Field(default=None, max_length=MAX_DISPLAY_NAME)
    #: Accepted only as an ECHO of the account's own proved number — see
    #: `update_me`. A person cannot type their identity.
    phone: UzPhoneOpt = Field(default=None, max_length=32)
    email: EmailOpt = Field(default=None, max_length=255)
    birthdate: str | None = None  # ISO date "YYYY-MM-DD" or "" to clear
    gender: str | None = None     # "male" | "female" | "" to clear
    locale: str | None = Field(default=None, max_length=8)


@router.patch("/me", response_model=MeResponse, response_model_by_alias=True)
async def update_me(
    body: ProfileUpdateIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MeResponse:
    """Self-service profile update — the signed-in user edits their own fields."""
    data = body.model_dump(exclude_unset=True)

    if "display_name" in data:
        # Normalised here, never by the client: what the column holds is the
        # server's decision.
        user.display_name = normalize_display_name(data["display_name"])
    if "phone" in data:
        # The account phone is not editable text. It is proved by sharing a
        # Telegram contact whose `contact.user_id` is the sender (see
        # `services/telegram_contact.py`) and by nothing else — so a free-text
        # box that wrote a second, unproved number was a second answer to an
        # identity question that must have one.
        #
        # An echo of the number already proved is accepted as the no-op it is,
        # so a client that round-trips the profile it was served still saves. A
        # DIFFERENT number is refused loudly rather than silently dropped: a
        # cheerful 200 that saves nothing is how "my number won't change" turns
        # into a support ticket instead of an answer.
        offered = (data["phone"] or "").strip()
        if offered and not same_number(offered, user.verified_phone):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "phone_not_editable",
            )
    if "email" in data:
        user.email = (data["email"] or "").strip() or None
    if "locale" in data and data["locale"] in {"uz", "ru"}:
        user.locale = data["locale"]
    if "gender" in data:
        g = (data["gender"] or "").strip().lower()
        if g in {"male", "female"}:
            user.gender = g
        elif g == "":
            user.gender = None
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_gender")
    if "birthdate" in data:
        raw = (data["birthdate"] or "").strip()
        if raw == "":
            user.birthdate = None
        else:
            try:
                user.birthdate = date.fromisoformat(raw)
            except ValueError:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_birthdate") from None

    await db.flush()
    await db.commit()
    return await me(user=user, db=db)
