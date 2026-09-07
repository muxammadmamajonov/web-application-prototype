"""Route dependencies that grant a database authority.

Batch 5 made ``muddato_app`` — the least-privileged authority — the default for
every transaction, so a handler that resolves nothing reaches no tenant data.
Everything above that default is granted **here**, explicitly, by a dependency
that runs only after the corresponding authorization check has passed:

    ``platform_authority``   after the caller is confirmed platform staff
    ``customer_authority``   after the caller has proved they own their number
    ``worker_authority``     on the secret-authenticated cron / bot surfaces

Declaring these at the *router* rather than sprinkling them through handlers is
deliberate: a new endpoint added to ``app/api/platform/`` inherits the platform
guard automatically, and a new endpoint added anywhere else inherits nothing.
Forgetting to opt in denies; you cannot forget to opt out.

The in-handler ``_require_platform`` / ``_require_super`` checks stay exactly
where they are. They are the authorization decision; this module only makes the
database agree with it.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import current_user
from app.db import DbAuthority, get_db, use_authority
from app.models import User
from app.models.enums import PlatformRole


async def _platform_authority(db: AsyncSession, user: User) -> None:
    """Grant cross-tenant HQ access — only to platform staff.

    The role check is repeated here rather than assumed from the handler's own
    ``_require_platform``: this dependency is what hands out the database
    authority, so it must not depend on a later call remembering to validate.
    """
    if user.platform_role not in (PlatformRole.super_admin, PlatformRole.support):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "platform_only")
    await use_authority(db, DbAuthority.platform)


async def _customer_authority(db: AsyncSession, user: User) -> None:
    """Grant the verified-customer authority.

    Granted to any authenticated account: the DATABASE, not this dependency,
    decides what an unverified account can see. ``app_verified_telegram_id()``
    returns NULL unless the account has a proved, eligible number, and every
    customer policy is keyed on it — so an unverified session under this
    authority reads nothing at all. Refusing here as well would only turn a
    silent empty result into a 403 on endpoints (``/me/identity``,
    ``/me/notifications``) that must answer for an unverified user too.
    """
    await use_authority(db, DbAuthority.customer)


async def _worker_authority(db: AsyncSession) -> None:
    """Grant cross-tenant background authority to cron and the bot.

    (Batch 5 independent-review correction) PUBLIC PROVIDER WEBHOOKS ARE NO
    LONGER IN THIS LIST, and must not be added back. They used to carry this at
    the router, which handed an unauthenticated HTTP request cross-tenant reach
    before it had proved anything — and the handlers then performed a global
    provider lookup with it, *before* checking the signature. They now start on
    the default authority, resolve one provider through a narrow capability, and
    narrow to the verified tenant. See ``app.core.webhook_identity``.

    What remains is authenticated by a secret the caller must already hold: the
    Telegram bot webhook path secret, and the internal cron secret. The
    authority still comes from the ROUTE, never from the absence of a header,
    and a handler that resolves an exact tenant narrows itself with
    ``set_tenant_scope``, which is strictly tighter than this grant.
    """
    await use_authority(db, DbAuthority.worker)


async def scope_to_object(
    db: AsyncSession, kind: str, object_id: object,
) -> str | None:
    """Narrow an unscoped capability request to the tenant that owns one object.

    Used by the paths that are authorized by a *capability over a named object*
    rather than by a tenant header: a signed download link, a signed receipt or
    media URL, a deep-link intent, a contract PDF. Each of these knows exactly
    which object it is about and nothing else, so before Batch 5 they ran with
    no scope at all — which, under the old policies, meant every tenant's rows.

    Two properties make this safe:

    * **It only ever narrows.** If the request already carries a tenant scope
      (an ordinary tenant route), this is a no-op. Re-pointing an existing scope
      at another tenant's object would be exactly the escalation the scope
      exists to prevent, so it is not possible here — the object simply stays
      invisible and the caller gets the repository's uniform 404.
    * **It grants reach, not authorization.** The caller's own access check runs
      afterwards and is unchanged. This narrows what the *database* would hand
      over if that check were ever forgotten; it never stands in for it.

    Returns the owning tenant id, or None when the object does not exist (or
    when RLS is off, where the whole mechanism is inert).
    """
    from sqlalchemy import text

    from app.config import settings
    from app.db import current_db_scope, set_tenant_scope

    if not settings.rls_enabled:
        return None
    if current_db_scope()["tenant_id"]:
        return None
    owner = (await db.execute(
        text("SELECT public.rls_owning_tenant(:kind, :oid)"),
        {"kind": kind, "oid": str(object_id)},
    )).scalar()
    if owner is None:
        return None
    await set_tenant_scope(db, owner)
    return str(owner)


# ── Router-level dependencies ────────────────────────────────────────────────
# Written out one by one rather than generated: FastAPI resolves these
# signatures at import time, and a factory-built closure carries the factory's
# locals in its annotations, which pydantic cannot resolve.


async def platform_authority(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> None:
    """Attach to the HQ router: platform staff only, cross-tenant reach."""
    await _platform_authority(db, user)


async def customer_authority(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(current_user)],
) -> None:
    """Attach to ``/me``: a signed-in person, scoped in SQL by proved phone."""
    await _customer_authority(db, user)


async def worker_authority(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Attach to webhook / cron / bot surfaces: explicit cross-tenant reach."""
    await _worker_authority(db)


#: ``identity_authority`` is deliberately ABSENT. There is no router-level
#: identity grant any more, and there must not be one: the authority it named
#: held ``USING (true) WITH CHECK (true)`` on ``users``, so attaching it to a
#: router gave every handler under that router — present and future — the whole
#: directory before any credential was checked. The bootstrap is now three
#: named operations in ``app.core.identity_bootstrap``, each borrowing the
#: authority for a single capability call, and the authority itself no longer
#: has any policy on ``users``. Re-adding a dependency here should mean
#: re-arguing that design, not re-importing a name.
__all__ = (
    "customer_authority",
    "platform_authority",
    "worker_authority",
)
