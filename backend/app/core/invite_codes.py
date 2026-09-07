"""One definition of what an invitation credential is.

Every mint, link-builder and lookup goes through this module. Before it, the
same prefixed t.me deep-link f-string was written out in four separate places
and parsed in two more — which is how an encoder/decoder mismatch survived
review: each site was individually plausible, and nothing forced them to agree.

WHAT AN INVITATION IS
    A bearer credential. Whoever holds the link can bind a Telegram account to
    a shop's customer record, so it has to be treated like a password, not like
    an id.

    Two properties follow, and the previous 6-character code had neither:

    * ENOUGH ENTROPY. 32**6 is about 2**30 — a billion, which is nothing to a
      machine and was only ever defensible because the lookup was rate-limited.
      Rate limiting protects the ONLINE guess; it does nothing about a leaked
      database. New credentials carry 192 bits.

    * NOT RECOVERABLE FROM STORAGE. The database keeps an HMAC-SHA256 verifier,
      never the credential. A dump therefore cannot reconstruct a working link.
      Note that hashing is only meaningful BECAUSE of the entropy above: a
      SHA-256 of a six-character code is a rainbow table, not a protection.

    The HMAC key is `SECRET_KEY` under its own domain label — deliberately not
    the Telegram bot token, which is shared with a third party and rotates for
    unrelated reasons.

RAW VALUES ARE SHOWN ONCE
    `mint` returns the credential; only the verifier is stored. Nothing can read
    a link back out afterwards, by design. A lost invitation is revoked and
    reissued, not recovered.

LEGACY LINKS
    Rows minted before this carry a plaintext `token` and no verifier. They keep
    resolving until their own `expires_at` passes and are never re-minted, so
    the plaintext population can only shrink.
"""
from __future__ import annotations

import hmac
import re
import secrets
from hashlib import sha256

from app.config import settings

#: 24 random bytes → 192 bits, 32 url-safe characters. Telegram's `startapp`
#: accepts [A-Za-z0-9_-] up to 64 characters, which is exactly this alphabet.
CREDENTIAL_BYTES = 24
MIN_CREDENTIAL_ENTROPY_BITS = 128

#: Domain separation: this key is used for other things, and a verifier must
#: not be interchangeable with any other digest the application computes.
_HMAC_LABEL = b"muddato-invitation-verifier-v1:"

# ── legacy (read-only) ──────────────────────────────────────────────────────
#: The retired 6-character shape. Recognised on the way IN so unexpired links
#: keep working; never minted.
LEGACY_CODE_LEN = 6
LEGACY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_RE = re.compile(rf"^[{re.escape(LEGACY_ALPHABET)}]{{{LEGACY_CODE_LEN}}}$", re.IGNORECASE)
LEGACY_PREFIXES = ("inv_", "join_")


def new_credential() -> str:
    """A fresh invitation credential. Returned to the caller exactly once."""
    return secrets.token_urlsafe(CREDENTIAL_BYTES)


def verifier(raw: str) -> str:
    """The non-reversible value stored for `raw`.

    `raw` is used exactly as given, minus outer whitespace. Any other
    normalisation here would silently produce a digest that does not match the
    one stored at mint time.

    Constant-time comparison is unnecessary at lookup: the digest is the index
    key, so the database compares it, and a timing signal over a 192-bit space
    buys an attacker nothing.
    """
    return hmac.new(
        settings.secret_key.encode(), _HMAC_LABEL + raw.strip().encode(), sha256,
    ).hexdigest()


def strip_legacy_prefix(raw: str) -> str:
    """Remove an `inv_` / `join_` prefix — but ONLY from a legacy code.

    A current credential is 32 url-safe characters drawn at random, so roughly
    one in a thousand legitimately begins `inv_` or `join_`. Stripping that
    would delete four characters of a 192-bit secret before hashing it, and the
    link would resolve to nothing for reasons nobody could reproduce.

    The prefix therefore only comes off when what remains is genuinely the
    retired six-character shape.
    """
    for p in LEGACY_PREFIXES:
        if raw.startswith(p) and looks_like_code(raw[len(p):]):
            return raw[len(p):]
    return raw


def looks_like_code(raw: str) -> bool:
    """True for the retired 6-char shape, in any casing."""
    return bool(CODE_RE.match(raw))


def legacy_token_candidates(raw: str) -> list[str]:
    """Stored plaintext values the input could mean. Legacy rows only.

    Case-insensitivity lives here rather than in a `lower(token)` index: old
    codes were stored upper-case, so upper-casing the input is a complete
    substitute. Mixed-case legacy tokens are matched exactly and never
    upper-cased — doing so would turn a miss into a wrong hit.
    """
    cleaned = strip_legacy_prefix((raw or "").strip())
    if not cleaned:
        return []
    out = [cleaned]
    if looks_like_code(cleaned) and cleaned.upper() != cleaned:
        out.append(cleaned.upper())
    return out


async def unlock(db, raw: str) -> str | None:
    """Grant this request access to the ONE invitation ``raw`` names.

    (Batch 5) An invitation credential is a bearer capability presented by
    somebody who is, by definition, not yet a member of anything — so the claim
    routes have no tenant scope and, before this, ran with none. Under the old
    fail-open policies that meant an unscoped read of every invitation in the
    system; under the new fail-closed ones it would mean reading none.

    So the credential is exchanged for a scope, in the database, by
    ``rls_resolve_invitation``: a SECURITY DEFINER function that takes the
    verifier (and the legacy plaintext candidates) and returns **two ids and
    nothing else**. There is no list access and no enumeration — the policy that
    follows matches a single invitation id — and a wrong credential resolves to
    nothing, which is the same answer as a credential for an invitation that
    never existed.

    A tenant-bound invitation additionally pins that tenant's scope, so the rest
    of the claim (creating the customer, the contract, the guarantors) runs
    inside exactly one shop. A platform-admin invitation has no tenant and pins
    none, so it reaches the invitation row and the user directory and no shop
    data at all.

    Returns the resolved tenant id (None for a platform invite, an unknown
    credential, or when RLS is off and the mechanism is inert).
    """
    from sqlalchemy import text

    from app.config import settings
    from app.db import set_invitation_capability, set_tenant_scope

    if not settings.rls_enabled:
        return None
    exact = (raw or "").strip()
    if not exact:
        return None
    row = (await db.execute(
        text("SELECT invitation_id, tenant_id FROM public.rls_resolve_invitation"
             "(:verifier, CAST(:legacy AS text[]))"),
        {"verifier": verifier(exact), "legacy": legacy_token_candidates(raw) or None},
    )).first()
    if row is None or row[0] is None:
        return None
    await set_invitation_capability(db, row[0])
    if row[1] is not None:
        await set_tenant_scope(db, row[1])
        return str(row[1])
    return None


def lookup_filter(raw: str):
    """The WHERE clause that finds the invitation for `raw`, if any.

    One clause for both populations, so no call site can accidentally support
    only one of them.
    """
    from sqlalchemy import false, or_

    from app.models import Invitation

    # The credential is OPAQUE. Outer transport whitespace is all that comes
    # off: no prefix, no case folding, no trimming of anything inside it. The
    # exact string that was minted has to be the exact input to the HMAC, or the
    # verifier will not match what is stored.
    exact = (raw or "").strip()
    if not exact:
        # Never `WHERE token IS NULL` — that would match every new row.
        return false()
    legacy = legacy_token_candidates(raw)
    return or_(
        Invitation.token_verifier == verifier(exact),
        Invitation.token.in_(legacy) if legacy else false(),
    )


async def mint(db) -> tuple[str, str]:
    """``(raw_credential, verifier)``. Store the verifier; hand over the raw once.

    No pre-check loop: at 192 bits a collision is not a thing that happens, and
    the UNIQUE index on the verifier is the real guarantee either way. Callers
    still retry on `IntegrityError` because that is how concurrent inserts are
    handled generally.
    """
    _ = db
    raw = new_credential()
    return raw, verifier(raw)


def deep_link(bot_username: str, code: str) -> str:
    """The t.me link that opens the Mini-App straight onto the join form.

    `startapp` (not `start`) is deliberate: it hands the value to
    `Telegram.WebApp.initDataUnsafe.start_param` without the recipient having to
    interact with the bot first. The bot's `/start` handler is only a fallback
    for clients too old to honour it.
    """
    return f"https://t.me/{bot_username}?startapp={code}"


def web_url(code: str) -> str:
    """Browser fallback — opens the same JoinPage outside Telegram."""
    return f"{settings.frontend_url.rstrip('/')}/i/{code}"
