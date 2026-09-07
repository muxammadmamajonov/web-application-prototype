"""(#209) A platform operator (super_admin/support) must not self-provision a
customer tenant — that would mix the operator identity with a shop-owner one.

The guard lives at the single chokepoint (``provision_tenant_from_intent``) so it
covers every entry point: the bot demo redemption, the web signup, and the
authenticated provision-trial. It short-circuits before any DB use, so this test
needs no database.
"""
from __future__ import annotations

import pytest

from app.models import User
from app.models.enums import PlatformRole
from app.services.onboarding import provision_tenant_from_intent


async def test_platform_admin_cannot_provision_tenant() -> None:
    for role in (PlatformRole.super_admin, PlatformRole.support):
        user = User(platform_role=role)
        with pytest.raises(ValueError, match="platform_admin_cannot_provision_tenant"):
            # db is never touched — the guard raises first; pass None deliberately.
            await provision_tenant_from_intent(None, user=user, intent={"business_name": "X"})  # type: ignore[arg-type]
