"""(#155) The Telegram webhook secret_token must be 1-256 chars of [A-Za-z0-9_-].
Both the shared (platform) and per-tenant secrets must satisfy that charset —
the shared one previously returned raw secret_key[:32] with no such guarantee."""
from __future__ import annotations

import re
from uuid import uuid4

from app.api.bot import _shared_secret, _tenant_secret

_TG_SECRET_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,256}")
_HEX32 = re.compile(r"[a-f0-9]{32}")


def test_shared_secret_is_telegram_charset_safe_and_stable() -> None:
    s = _shared_secret()
    assert _TG_SECRET_TOKEN.fullmatch(s), f"not a valid Telegram secret_token: {s!r}"
    assert _HEX32.fullmatch(s)            # hex digest, 32 chars
    assert _shared_secret() == s          # deterministic


def test_tenant_secret_is_telegram_charset_safe() -> None:
    s = _tenant_secret(uuid4())
    assert _TG_SECRET_TOKEN.fullmatch(s)
    assert _HEX32.fullmatch(s)
