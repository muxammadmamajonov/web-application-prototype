"""Cross-domain pieces of the platform API: the HQ authorization guards and the
constants more than one domain reads.

Domain modules import from here; nothing here imports a domain module, so the
package has no internal cycles.
"""
from __future__ import annotations

from fastapi import HTTPException, status

from app.core import invite_codes
from app.models import (
    User,
)
from app.models.enums import (
    PlatformRole,
)


def _require_platform(user: User) -> None:
    if user.platform_role not in (PlatformRole.super_admin, PlatformRole.support):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "platform_only")

def _display_name(u: User | None) -> str | None:
    """A human label for an actor, so HQ screens show people and not UUIDs.

    Degrades real name → ``@username`` → ``None``. Never invents a placeholder:
    an unresolved actor is absent, and the UI omits the row rather than showing
    a name nobody can be held to.
    """
    if u is None:
        return None
    full = f"{u.first_name or ''} {u.last_name or ''}".strip()
    return full or (f"@{u.telegram_username}" if u.telegram_username else None)

#: How many recent audit rows the action-filter vocabulary is derived from. The
#: action set is application-authored and small, so a bounded recent window
#: covers it in practice while keeping the audit screen's cost independent of
#: table size.
AUDIT_ACTION_SCAN_LIMIT = 5_000

#: How long a freshly-minted owner-invite link stays valid.
OWNER_INVITE_HOURS = 72

def _owner_invite_url(token: str) -> str:
    """Deep link to a single-use owner-claim token. The first person to open it
    and confirm becomes the business owner; the token is then consumed and the
    link can't be reused or guessed (unlike the old ``business_<slug>`` link)."""
    from app.config import settings
    return invite_codes.deep_link(settings.telegram_bot_username, token)

def _require_super(user: User) -> None:
    if user.platform_role != PlatformRole.super_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "super_admin_only")
