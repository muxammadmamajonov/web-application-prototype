"""Structural characterization of the consolidated platform-admin guard.

Role behaviour is covered by ``test_security_fixes.py``. What is asserted here is
what that cannot see: that the three routers share one implementation, and that
no surviving endpoint lost its guard.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.api import bot, catalog, cron
from app.core.authz import require_platform_admin


def test_routers_share_the_canonical_guard() -> None:
    """One implementation, three consumers — a role change cannot apply to only some."""
    for module in (catalog, cron, bot):
        assert module.require_platform_admin is require_platform_admin, module.__name__


def test_guarded_call_sites_are_not_lost() -> None:
    """Pins the surviving guarded endpoints — a count may fall only when a route is deliberately removed."""
    api = Path(__file__).resolve().parents[1] / "app" / "api"
    for filename, want in {"catalog.py": 10, "cron.py": 2, "bot.py": 3}.items():
        src = (api / filename).read_text(encoding="utf-8")
        found = len(re.findall(r"^\s+require_platform_admin\(", src, re.M))
        assert found == want, f"{filename}: expected {want} guarded call sites, found {found}"
