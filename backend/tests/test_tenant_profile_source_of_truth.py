"""The shop's public profile has ONE resolution, and every surface uses it.

THE DUPLICATE (audit, 2026-08-11). The shop's name, logo, address and contact
number live in two tables — `tenants` (the platform's provisioning record) and
`tenant_settings` (the profile its owner edits) — and six render sites each
decided for themselves which one wins. Four different rules for the logo alone:

    contract PDF          settings.logo_url if settings else tenant.logo_url
    PDF preview           settings.logo_url if settings else tenant.logo_url
    /me contract list     tenant.logo_url
    /me contract detail   tenant.logo_url
    invitation page       s.logo_url if s else tenant.logo_url
    settings screen       s.logo_url

Two of those reached users. `tenants.logo_url` has **no writer anywhere in the
codebase**, so the two `/me` surfaces showed a shop with no logo forever while
the same shop's contracts rendered one. And the PDF rendered `tenant.name`
rather than the `business_name` its owner typed, so a shop that renamed itself
kept the old name on every contract it generated.

Production had both drifts with two tenants on the books: one name disagreement
and one phone disagreement.

The tests below pin the RULE (in `services/tenant_profile`) and then pin that
each surface actually goes through it — because the defect was never that the
rule was wrong, it was that there were six of them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Tenant, TenantSettings
from app.services.tenant_profile import resolve

APP = Path(__file__).resolve().parents[1] / "app"


def _tenant(**kw) -> Tenant:
    base = {"slug": "shop", "name": "Provisioned Name", "logo_url": None,
            "contact_phone": None}
    return Tenant(**{**base, **kw})


def _settings(**kw) -> TenantSettings:
    return TenantSettings(**kw)


# ── the rule ─────────────────────────────────────────────────────────────────

def test_the_owners_own_name_wins_over_the_provisioning_one() -> None:
    """The drift that reached production: a renamed shop kept its old name."""
    p = resolve(_tenant(name="Old Ltd"), _settings(business_name="Demo Electronics"))
    assert p.name == "Demo Electronics"


def test_the_provisioning_name_stands_in_when_the_owner_set_none() -> None:
    assert resolve(_tenant(name="Provisioned Name"), None).name == "Provisioned Name"
    assert resolve(_tenant(name="Provisioned Name"), _settings()).name == "Provisioned Name"


def test_a_blank_name_is_absent_not_a_nameless_shop() -> None:
    """Whitespace must fall through, or a shop renders with no name at all."""
    p = resolve(_tenant(name="Provisioned Name"), _settings(business_name="   "))
    assert p.name == "Provisioned Name"


def test_the_logo_comes_from_the_profile_the_owner_can_actually_edit() -> None:
    p = resolve(_tenant(logo_url=None), _settings(logo_url="https://cdn/x.png"))
    assert p.logo_url == "https://cdn/x.png"


def test_a_legacy_tenant_logo_is_still_readable() -> None:
    """`tenants.logo_url` is deprecated and unwritten — but a row that carries
    one must not lose it just because the writer is gone."""
    assert resolve(_tenant(logo_url="https://cdn/legacy.png"), None).logo_url \
        == "https://cdn/legacy.png"


def test_the_contact_number_falls_back_to_what_the_platform_recorded() -> None:
    """A shop that never opened Settings still has to be reachable."""
    p = resolve(_tenant(contact_phone="+998901112233"), _settings())
    assert p.phone == "+998901112233"
    # …and the owner's own number wins once they set one.
    p2 = resolve(_tenant(contact_phone="+998901112233"),
                 _settings(contact_phone="+998907776655"))
    assert p2.phone == "+998907776655"


def test_settings_only_fields_do_not_invent_a_second_source() -> None:
    t = _tenant()
    assert resolve(t, None).address is None
    assert resolve(t, None).email is None
    assert resolve(t, None).telegram is None


# ── every surface goes through it ────────────────────────────────────────────

SURFACES = [
    ("services/pdf.py", "the contract PDF and its preview"),
    ("api/me.py", "the customer's contract list and detail"),
    ("api/invitations.py", "the invitation landing page"),
    ("api/tenant_settings.py", "the owner's own settings screen"),
]


@pytest.mark.parametrize("rel,what", SURFACES)
def test_the_surface_resolves_through_the_shared_module(rel: str, what: str) -> None:
    src = (APP / rel).read_text(encoding="utf-8")
    assert "tenant_profile" in src, (
        f"{what} ({rel}) must resolve the shop profile through "
        f"services/tenant_profile, not with its own precedence"
    )


@pytest.mark.parametrize("rel,what", SURFACES)
def test_no_surface_reads_the_dead_tenant_logo_column(rel: str, what: str) -> None:
    """`tenants.logo_url` has no writer. A reader outside the resolver is a
    surface that will render an empty logo forever."""
    src = (APP / rel).read_text(encoding="utf-8")
    offenders = [
        line.strip() for line in src.splitlines()
        if ("tenant.logo_url" in line or "t.logo_url" in line)
        and not line.strip().startswith("#")
    ]
    assert offenders == [], (
        f"{what} ({rel}) still reads the unwritten tenants.logo_url:\n"
        + "\n".join(offenders)
    )


def test_no_surface_keeps_its_own_profile_fallback_chain() -> None:
    """The `a or b` chains this consolidation removed must not come back.

    Each of these was a different answer to the same question, and the point of
    the resolver is that the question is answered once.
    """
    banned = (
        "settings.contact_phone if settings else None) or tenant.contact_phone",
        "s.logo_url if s else tenant.logo_url",
        "s.business_name or (tenant.name if tenant else None)",
    )
    offenders: list[str] = []
    for rel, _ in SURFACES:
        src = (APP / rel).read_text(encoding="utf-8")
        for pattern in banned:
            if pattern in src:
                offenders.append(f"{rel}: {pattern}")
    assert offenders == [], "a per-surface profile fallback chain is back:\n" + "\n".join(offenders)


def test_tenants_logo_url_still_has_no_writer() -> None:
    """The column is deprecated. Adding a writer would re-create the split this
    consolidation removed — two editable logos, and no rule about which wins."""
    offenders: list[str] = []
    for path in APP.rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "models/" in str(path):
                continue
            if "tenant.logo_url =" in stripped or "tenant.logo_url=" in stripped:
                offenders.append(f"{path.relative_to(APP)}:{i}  {stripped}")
    assert offenders == [], (
        "tenants.logo_url is deprecated and must stay unwritten:\n" + "\n".join(offenders)
    )
