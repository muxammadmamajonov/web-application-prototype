"""(#129) Guard: every audit.record(action="x.y") literal in the app must be in
AUDITED_ACTIONS — otherwise audit.record() silently drops the row. This caught 14
recorded-but-dropped actions; the test stops the gap from re-opening.
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services.audit import AUDITED_ACTIONS

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
_ACTION_LITERAL = re.compile(r'action="([a-z_]+\.[a-z_]+)"')


def test_every_recorded_action_is_audited() -> None:
    used: set[str] = set()
    for py in _APP_DIR.rglob("*.py"):
        used |= set(_ACTION_LITERAL.findall(py.read_text(encoding="utf-8")))
    assert used, "no action= literals found — regex/layout changed?"
    missing = sorted(used - AUDITED_ACTIONS)
    assert not missing, (
        "audit.record(action=...) used but NOT in AUDITED_ACTIONS "
        f"(silently dropped): {missing}"
    )
