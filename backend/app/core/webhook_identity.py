"""Provider resolution for PUBLIC webhooks — the step before authentication.

THE PROBLEM THIS SOLVES
-----------------------
A provider callback arrives unauthenticated. To check its signature we first
have to find the merchant configuration it claims to be signed with, and that
lookup is inherently cross-tenant: the request carries a Click ``service_id`` or
a Payme Basic-auth secret, not a tenant header.

Batch 5 answered that by attaching ``worker_authority`` — full cross-tenant
reach — to the whole webhook *router*. So an unauthenticated HTTP request held
cross-tenant database permission from the moment it was routed, ran a global
``SELECT … FROM tenant_settings`` with it, and only afterwards verified the
signature. Anything added to those routers later inherited the same grant.

WHAT REPLACES IT
----------------
Each function here borrows ``muddato_webhook`` — an authority that is **not a
member of** ``muddato_runtime`` and therefore holds no privilege on any table —
for exactly one call to a narrow ``SECURITY DEFINER`` resolver, then puts the
authority back. Each resolver:

* takes only the minimal public identifier the protocol already puts on the wire;
* returns at most one row, of exactly what signature verification needs;
* is owned by the migration owner with a pinned ``search_path``;
* has ``EXECUTE`` revoked from ``PUBLIC`` **and from** ``muddato_runtime``, and
  granted to ``muddato_webhook`` alone — so an ordinary tenant handler cannot
  call ``resolve_click_provider`` with a competitor's ``service_id``.

The caller then verifies, rejects uniformly on failure, and only on success
narrows itself to the resolved tenant. See ``docs/RLS.md``.

WHEN ``rls_enabled`` IS OFF
---------------------------
The authority layer is inert (``app/db.py``) and the database may be a
model-only schema with no capability functions, so these fall back to the plain
ORM query. That is not a credential fallback: with the layer off the
*application* login still reaches nothing it is not granted, and production
runs with it on. ``app.cli.rls_preflight`` refuses to serve traffic in any
posture where that is not true.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import DbAuthority, capability

#: Upper bound on rows any resolver here will return. Every one of them is
#: keyed on a unique column, so >1 would mean the schema changed under us.
_ONE = 1


@dataclass(frozen=True, slots=True)
class ProviderCredential:
    """One merchant's tenant and its stored (still encrypted) signing secret."""

    tenant_id: UUID
    secret_ciphertext: str


async def resolve_click_provider(
    db: AsyncSession, service_id: str,
) -> ProviderCredential | None:
    """The tenant + Click secret for a ``service_id``, or None.

    ``service_id`` is the merchant identifier Click puts in the callback body —
    public by construction, and useless on its own: the row it names yields only
    the *encrypted* secret, and the caller still has to reproduce the signature.
    """
    if not service_id:
        return None
    if not settings.rls_enabled:
        from app.models import TenantSettings

        row = (await db.execute(
            select(TenantSettings.tenant_id, TenantSettings.click_secret_key)
            .where(
                TenantSettings.click_service_id == service_id,
                TenantSettings.click_secret_key.is_not(None),
            )
            .limit(_ONE)
        )).first()
        return ProviderCredential(row[0], row[1]) if row else None

    async with capability(db, DbAuthority.webhook):
        row = (await db.execute(
            text("SELECT tenant_id, secret_ciphertext "
                 "FROM public.rls_webhook_click_provider(:sid)"),
            {"sid": service_id},
        )).first()
    return ProviderCredential(row[0], row[1]) if row else None


async def resolve_payme_provider(
    db: AsyncSession, presented_secret: str,
) -> ProviderCredential | None:
    """The tenant + Payme secret matching ``presented_secret``, or None.

    Keyed on a blind index of the secret the caller already supplied, so
    producing an argument that matches anything requires holding the credential
    the lookup would otherwise have proved. There is no enumeration here to do.
    """
    if not presented_secret:
        return None
    from app.core.crypto import blind_index

    bidx = blind_index(presented_secret, context="payme_secret")
    if not settings.rls_enabled:
        from app.models import TenantSettings

        row = (await db.execute(
            select(TenantSettings.tenant_id, TenantSettings.payme_secret_key)
            .where(
                TenantSettings.payme_secret_bidx == bidx,
                TenantSettings.payme_secret_key.is_not(None),
            )
            .limit(_ONE)
        )).first()
        return ProviderCredential(row[0], row[1]) if row else None

    async with capability(db, DbAuthority.webhook):
        row = (await db.execute(
            text("SELECT tenant_id, secret_ciphertext "
                 "FROM public.rls_webhook_payme_provider(:bidx)"),
            {"bidx": bidx},
        )).first()
    return ProviderCredential(row[0], row[1]) if row else None


async def resolve_billing_provider(db: AsyncSession, name: str) -> dict[str, Any] | None:
    """The PLATFORM's own merchant config for one provider, or None.

    The subscription callback settles money into the platform's account, not a
    tenant's, so the credential it is checked against is a single key inside the
    ``billing_providers`` platform setting. This returns that one provider's
    object — never the whole setting, never another key, and never through the
    ordinary ``platform_settings`` read policy, which deliberately allowlists
    only tenant-safe keys.
    """
    if not name:
        return None
    if not settings.rls_enabled:
        from app.services import subscription_checkout as sc

        providers = await sc.get_providers(db)
        got = providers.get(name)
        return got if isinstance(got, dict) else None

    async with capability(db, DbAuthority.webhook):
        got = (await db.execute(
            text("SELECT public.rls_webhook_billing_provider(:name)"),
            {"name": name},
        )).scalar()
    if isinstance(got, str):
        import json

        try:
            got = json.loads(got)
        except ValueError:
            return None
    return got if isinstance(got, dict) else None


async def resolve_topup_tenant(db: AsyncSession, topup_id: object) -> UUID | None:
    """Which tenant owns this top-up — an identifier, so a verified callback can
    narrow itself before reading or writing the row."""
    try:
        tid = UUID(str(topup_id))
    except (TypeError, ValueError):
        return None
    if not settings.rls_enabled:
        from app.models import TenantTopup

        return (await db.execute(
            select(TenantTopup.tenant_id).where(TenantTopup.id == tid)
        )).scalar_one_or_none()

    async with capability(db, DbAuthority.webhook):
        got = (await db.execute(
            text("SELECT public.rls_webhook_topup_tenant(:tid)"), {"tid": str(tid)},
        )).scalar()
    return got


async def resolve_topup_tenant_by_ref(db: AsyncSession, ref: str) -> UUID | None:
    """Same, keyed on the provider's own opaque transaction id.

    Payme's ``CheckTransaction`` / ``CancelTransaction`` name a transaction, not
    an account, so the tenant has to be reached through the stored provider ref.
    """
    if not ref:
        return None
    if not settings.rls_enabled:
        from app.models import TenantTopup

        return (await db.execute(
            select(TenantTopup.tenant_id).where(TenantTopup.provider_ref == ref).limit(_ONE)
        )).scalars().first()

    async with capability(db, DbAuthority.webhook):
        got = (await db.execute(
            text("SELECT public.rls_webhook_topup_tenant_by_ref(:ref)"), {"ref": ref},
        )).scalar()
    return got


__all__ = (
    "ProviderCredential",
    "resolve_billing_provider",
    "resolve_click_provider",
    "resolve_payme_provider",
    "resolve_topup_tenant",
    "resolve_topup_tenant_by_ref",
)
