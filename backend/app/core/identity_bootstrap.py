"""The pre-authentication identity bootstrap — three operations, not a directory.

THE PROBLEM THIS SOLVES
-----------------------
Somebody has to be able to find or create an account *before* anyone is signed
in: the Telegram Login Widget callback, the OIDC callback, Mini-App initData, an
invitation claim. Batch 5 answered that by giving ``muddato_identity`` a
``users`` policy of ``USING (true) WITH CHECK (true)`` and attaching that
authority to the whole ``/auth`` router. So every handler under ``/auth`` — and
every handler added there later — ran with read and write access to the entire
user directory before a single credential had been checked.

WHAT REPLACES IT
----------------
``muddato_identity`` now has **no policy on ``users`` at all**, so its direct
access is deny-all: it cannot list, select, update or delete a row. The
legitimate bootstrap operations are enumerated here, each backed by a narrow
``SECURITY DEFINER`` function with a pinned ``search_path``, ``EXECUTE`` revoked
from ``PUBLIC`` and from ``muddato_runtime``, and granted to
``muddato_identity`` alone (migration ``rg0credsplit``).

Each operation:

* is reached only through a **credential-derived identifier** — a Telegram id
  from validated initData, a verified OIDC ``sub``, never a search term;
* returns identifiers and state flags, never a row of personal data;
* cannot set ``platform_role``, ``is_active``, ``verified_phone``,
  ``verified_phone_source``, ``phone_eligibility``, ``tokens_valid_from`` or any
  other privileged field — those are absent from the SQL function's parameter
  list, so mass assignment is impossible by signature rather than by discipline;
* borrows the identity authority for exactly one statement and hands it back.

AFTER VERIFICATION
------------------
Nothing here is used once a session exists. A signed-in request pins
``app.user_id`` (``core.security.current_user``) and reads and writes its own
row through the ordinary ``muddato_app`` self policy — which is why the
``/auth`` router no longer carries a blanket authority at all.

WHEN ``rls_enabled`` IS OFF
---------------------------
The authority layer is inert and the database may be a model-only schema with no
capability functions, so these fall back to the ORM. Production runs with it on
and ``app.cli.rls_preflight`` refuses to serve traffic otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import DbAuthority, capability, set_identity_scope
from app.models import User

#: Hard ceiling on bot-token candidates, mirrored in the SQL function. A forged
#: initData must not amplify into an unbounded crypto scan.
MAX_BOT_TOKEN_CANDIDATES = 500


@dataclass(frozen=True, slots=True)
class IdentityState:
    """What the invitation preview needs about the caller's OWN account.

    Five scalars, all of them the caller's OWN. No name, no email, no
    birthdate, no platform role, no membership — enough to render the
    verification state of the person who just proved this Telegram id, and
    useless for probing anybody else's.

    ``verified_phone`` is included because the preview has to say whether the
    proved number is the one the invitation was minted for, and it can only be
    the caller's: the Telegram id it is keyed on came from a validated initData
    HMAC.
    """

    user_id: UUID
    verified: bool
    eligibility: str | None
    locale: str | None
    verified_phone: str | None
    #: The name the PERSON owns, by the same precedence as
    #: ``services.display_names.person_name`` — computed inside the SQL function
    #: so ``display_name``/``first_name``/``last_name``/``platform_role`` never
    #: leave the database as separate columns.
    known_name: str | None


async def upsert_telegram_account(
    db: AsyncSession,
    *,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    photo_url: str | None = None,
    locale: str | None = None,
) -> UUID | None:
    """Find-or-create the account for a PROVED Telegram id; return its id.

    ``telegram_id`` must come from something already verified — a validated
    initData HMAC, a Login-Widget HMAC, or the ``sub`` claim of an id_token
    checked against Telegram's JWKS. This function does not verify anything; it
    is the write that a verification has earned.

    Concurrency: the underlying ``INSERT … ON CONFLICT (telegram_id) DO UPDATE``
    makes two simultaneous first logins produce ONE account. The unique index
    arbitrates, so there is no read-then-write window to lose.

    Only Telegram-owned display fields are refreshed, and only when non-empty:
    Telegram omits ``last_name`` for people who have none, and letting an absent
    field blank a stored one loses data on every login.
    """
    if not telegram_id:
        return None
    if not settings.rls_enabled:
        return await _upsert_via_orm(
            db, telegram_id=telegram_id, username=username, first_name=first_name,
            last_name=last_name, photo_url=photo_url, locale=locale,
        )
    async with capability(db, DbAuthority.identity):
        got = (await db.execute(
            text(
                "SELECT public.rls_identity_upsert_telegram("
                ":tg, :username, :first_name, :last_name, :photo_url, :locale)"
            ),
            {
                "tg": int(telegram_id), "username": username,
                "first_name": first_name, "last_name": last_name,
                "photo_url": photo_url, "locale": locale,
            },
        )).scalar()
    return got


async def identity_state(db: AsyncSession, telegram_id: int) -> IdentityState | None:
    """The phone-proof state of the Telegram id the caller has just proved.

    Pass only a credential-derived id. Callers use it to decide which
    verification screen to render; it deliberately cannot answer "does account X
    exist" for an id the caller has not authenticated as.
    """
    if not telegram_id:
        return None
    if not settings.rls_enabled:
        # Distinct names for the two shapes: the ORM path yields a ``User``, the
        # capability path a row tuple, and reusing one name for both is what
        # made mypy — correctly — complain that a ``User`` is not indexable.
        found = (await db.execute(
            select(User).where(User.telegram_id == telegram_id)
        )).scalars().first()
        if found is None:
            return None
        from app.services.display_names import person_name

        return IdentityState(
            found.id, found.verified_phone is not None, found.phone_eligibility,
            found.locale, found.verified_phone, person_name(found),
        )

    async with capability(db, DbAuthority.identity):
        row = (await db.execute(
            text("SELECT user_id, verified, eligibility, locale, verified_phone, "
                 "known_name FROM public.rls_identity_user_state(:tg)"),
            {"tg": int(telegram_id)},
        )).first()
    if row is None:
        return None
    return IdentityState(row[0], bool(row[1]), row[2], row[3], row[4], row[5])


async def bot_token_candidates(
    db: AsyncSession, limit: int = MAX_BOT_TOKEN_CANDIDATES,
) -> list[str]:
    """Encrypted per-tenant bot tokens, for Mini-App initData verification.

    A tenant's own @BotFather bot opens the same Mini App, and Telegram signs
    that initData with the TENANT's token — but the payload carries no tenant
    hint, so verification has to try the configured tokens. This returns the
    ciphertext column and nothing else: no tenant id, no other setting, nothing
    that says whose token any given row is.

    RESIDUAL RISK & SECURITY FIX (MUDD-25 B1):
    ──────────────────────────────────────────
    The fallback to tenant bot tokens is DISABLED (returns empty list) due to
    account takeover vulnerability: without tenant membership validation, anyone
    with a tenant's bot token can login as any user. The proper fix requires
    tenant hint from deep-link start_param to identify which tenant to verify
    against, then validate user is a member of that tenant before issuing JWT.

    Until deep-link tenant hint is wired (frontend change), this fallback must
    remain disabled to prevent complete account takeover.

    Note this path was previously DEAD under RLS: ``rls_tenant_settings_identity``
    keys on ``app.user_id``, which is by definition unset before login, so the
    fallback silently found zero tokens and every tenant-bot login 401'd.
    """
    # MUDD-25 B1: Kill-switch - disable tenant bot token fallback
    # to prevent account takeover vulnerability
    return []


async def load_self(db: AsyncSession, user_id: object) -> User | None:
    """Load the account the caller has just been established as.

    Pins ``app.user_id`` first, so the read is satisfied by the ordinary
    ``muddato_app`` self policy — no capability, no elevated authority. This is
    the boundary: everything before it is bootstrap, everything after it is an
    ordinary signed-in request acting on its own row.
    """
    if user_id is None:
        return None
    await set_identity_scope(db, user_id)
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


async def _upsert_via_orm(
    db: AsyncSession,
    *,
    telegram_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    photo_url: str | None,
    locale: str | None,
) -> UUID:
    """The ``rls_enabled=False`` path: same semantics, plain ORM.

    Kept byte-compatible with the SQL function's field rules (non-empty values
    only, locale applied on create) so the two tiers cannot disagree about what
    a login writes.
    """
    from datetime import UTC, datetime

    user = (await db.execute(
        select(User).where(User.telegram_id == telegram_id)
    )).scalar_one_or_none()
    if user is None:
        user = User(
            telegram_id=telegram_id,
            telegram_username=username or None,
            first_name=first_name or None,
            last_name=last_name or None,
            photo_url=photo_url or None,
            locale=locale if locale in ("uz", "ru") else "uz",
        )
        db.add(user)
    else:
        if username:
            user.telegram_username = username
        if first_name:
            user.first_name = first_name
        if last_name:
            user.last_name = last_name
        if photo_url:
            user.photo_url = photo_url
    user.last_seen_at = datetime.now(UTC)
    await db.flush()
    return user.id


__all__ = (
    "MAX_BOT_TOKEN_CANDIDATES",
    "IdentityState",
    "bot_token_candidates",
    "identity_state",
    "load_self",
    "upsert_telegram_account",
)
