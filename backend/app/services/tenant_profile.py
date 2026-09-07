"""The shop's public profile — one resolution of it, for every surface.

WHY THIS EXISTS. The same business facts — the shop's name, logo, address and
contact number — live in two tables, and six render sites each decided for
themselves which one wins:

    contract PDF        name: tenant.name       logo: settings ?? tenant.logo_url
    PDF preview         name: tenant.name       logo: settings ?? tenant.logo_url
    /me (contract list) name: tenant.name       logo: tenant.logo_url
    /me (contract detail) name: tenant.name     logo: tenant.logo_url
                          phone: settings.contact_phone or tenant.contact_phone
    invitation page     name: tenant.name       logo: settings ?? tenant.logo_url
    settings screen     name: settings.business_name or tenant.name

Four different rules for the logo alone. Two consequences reached real users:

  * `tenants.logo_url` has NO writer anywhere in the codebase — nothing has been
    able to set it since the business profile moved to `tenant_settings`. So the
    two `/me` surfaces, which read it directly, show a shop with no logo forever
    while its contracts render one.
  * the PDF renders `tenant.name`, the row the platform stamped at provisioning,
    not `business_name`, the name the owner actually typed into Settings. A shop
    that renames itself keeps the old name on every contract it generates —
    which is a legal document.

Production had both drifts with two tenants on the books (2026-08-11: one
name disagreement, one phone disagreement).

THE RULE, stated once, here.

`tenant_settings` is the owner-editable business profile and therefore the
source of truth for everything a customer sees. `tenants` holds the platform's
provisioning record: the name an operator typed when creating the shop and the
number they recorded for it. That record seeds the profile and remains the
fallback for a shop whose owner has never opened Settings — it is not a second
place to maintain the same fact, and nothing customer-facing should read it
directly.

`tenants.logo_url` is DEPRECATED — unwritten, kept readable only so that a row
which somehow carries one does not lose it. Do not add a writer.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import Tenant, TenantSettings


def _clean(value: str | None) -> str | None:
    """A stored value that is present and not just whitespace, else None.

    Blank counts as absent on purpose: an empty `business_name` must fall
    through to the provisioning name rather than render a shop with no name.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True, slots=True)
class TenantProfile:
    """What a customer, a contract or an invitation should show about a shop."""

    name: str
    slug: str
    logo_url: str | None
    address: str | None
    phone: str | None
    email: str | None
    telegram: str | None
    hours: dict | None


def resolve(tenant: Tenant, settings: TenantSettings | None) -> TenantProfile:
    """The shop's public profile. THE only place the two tables are combined."""
    return TenantProfile(
        # The owner's own name for their business wins over the one an operator
        # typed at provisioning.
        name=_clean(getattr(settings, "business_name", None)) or tenant.name,
        slug=tenant.slug,
        # Settings only, in practice: the tenant column has no writer. Read as a
        # last resort so a legacy row keeps whatever it holds.
        logo_url=_clean(getattr(settings, "logo_url", None)) or _clean(tenant.logo_url),
        address=_clean(getattr(settings, "address", None)),
        # A shop that has never opened Settings still has the number the platform
        # recorded for it, and a customer needs to be able to call someone.
        phone=_clean(getattr(settings, "contact_phone", None)) or _clean(tenant.contact_phone),
        email=_clean(getattr(settings, "contact_email", None)),
        telegram=_clean(getattr(settings, "contact_telegram", None)),
        hours=getattr(settings, "business_hours", None) or None,
    )
