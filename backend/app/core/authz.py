"""Authorization guards shared by more than one router."""
from __future__ import annotations

from fastapi import HTTPException, status

from app.models import User
from app.models.enums import PlatformRole

# `support` is deliberately included: it is read-mostly but must reach operator
# surfaces. Work that must be super-admin-only uses its own narrower guard.
PLATFORM_ADMIN_ROLES = (PlatformRole.super_admin, PlatformRole.support)


def require_platform_admin(user: User) -> None:
    """Reject anyone who is not platform staff.

    The ``(403, "platform_admin_only")`` pair is a contract the frontend and
    operator tooling match on — do not change it.
    """
    if user.platform_role not in PLATFORM_ADMIN_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "platform_admin_only")
