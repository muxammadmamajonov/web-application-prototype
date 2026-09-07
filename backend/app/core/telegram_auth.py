"""Telegram authentication helpers.

Two verification paths:

* **Login Widget** (browser) — payload is a flat JSON dict, signed with
  ``HMAC_SHA256(SHA256(bot_token), data_check_string)``.
* **Mini-App initData** (in-Telegram WebApp) — payload is a query string,
  signed with ``HMAC_SHA256(HMAC_SHA256(b"WebAppData", bot_token), data_check_string)``.

See https://core.telegram.org/widgets/login and
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl

from pydantic import BaseModel, Field


class TelegramLoginPayload(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: int
    hash: str = Field(min_length=32, max_length=128)

    def data_check_string(self) -> str:
        """Sorted ``key=value`` pairs joined by \\n, hash excluded."""
        pairs = []
        for k in sorted(self.model_fields.keys()):
            if k == "hash":
                continue
            v = getattr(self, k)
            if v is None:
                continue
            pairs.append(f"{k}={v}")
        return "\n".join(pairs)


def _secret_key(bot_token: str) -> bytes:
    return hashlib.sha256(bot_token.encode()).digest()


def verify_login(payload: TelegramLoginPayload, *, bot_token: str, max_age_seconds: int = 86_400) -> bool:
    """Return True iff the payload's HMAC matches and the auth_date is fresh."""
    if time.time() - payload.auth_date > max_age_seconds:
        return False
    expected = hmac.new(
        _secret_key(bot_token),
        payload.data_check_string().encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, payload.hash.lower())


# ── Mini-App initData ───────────────────────────────────────────────────────

#: Default acceptance window for Mini-App ``initData`` (MUDD-76).
#:
#: Was 24h, which is how long a captured initData stayed usable. A Mini-App
#: launch presents its initData within seconds, so an hour is already generous
#: for a slow client or a retried request, and it cuts the replay surface 24×
#: for anything the single-use claim cannot cover (e.g. a purpose that has not
#: been wired to the seen-store yet). This module stays settings-free (it is
#: pure crypto, imported by tests without an environment); the endpoints pass
#: ``settings.telegram_initdata_max_age_seconds``, which defaults to this value
#: and is tunable per deployment via ``TELEGRAM_INITDATA_MAX_AGE_SECONDS``.
INITDATA_MAX_AGE_SECONDS = 3_600


class WebAppUser(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    language_code: str | None = None
    is_premium: bool | None = None
    is_bot: bool | None = None


class WebAppInitData(BaseModel):
    """Parsed and verified ``window.Telegram.WebApp.initData``."""
    user: WebAppUser
    auth_date: int
    #: The verified ``hash`` field — the signature that makes this exact string
    #: reusable until ``auth_date`` ages out. Surfaced (MUDD-76) so the login
    #: path can spend it once via ``app.core.initdata_replay``; treat it as a
    #: live credential and never log or return it.
    hash: str
    query_id: str | None = None
    chat_type: str | None = None
    chat_instance: str | None = None
    start_param: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def verify_oauth_result(data: dict[str, Any], *, bot_token: str, client_secret: str | None = None,
                        max_age_seconds: int = 86_400) -> bool:
    """Verify the ``tgAuthResult`` JSON returned by oauth.telegram.org.

    The redirect callback yields a base64-encoded JSON like::

        {"id": 12345, "first_name": "X", ..., "auth_date": 1721, "hash": "abc..."}

    The hash is HMAC_SHA256 over the sorted ``key=value\\n…`` payload using
    ``SHA256(bot_token)`` as the secret. If a `client_secret` is provided we
    also accept HMAC using it directly (newer Telegram OAuth installs).
    """
    if "hash" not in data or "auth_date" not in data:
        return False
    payload = {k: v for k, v in data.items() if k != "hash" and v is not None}
    try:
        auth_date = int(payload.get("auth_date", 0))
    except (TypeError, ValueError):
        return False
    if auth_date <= 0 or time.time() - auth_date > max_age_seconds:
        return False
    received = str(data["hash"]).lower()
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))

    # Primary: Login Widget HMAC scheme (SHA256(bot_token) as key)
    expected_bot = hmac.new(_secret_key(bot_token), check_str.encode(), hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected_bot, received):
        return True
    # Fallback: HMAC with client_secret directly (some OAuth installs use this).
    if client_secret:
        expected_cs = hmac.new(client_secret.encode(), check_str.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected_cs, received):
            return True
    return False


def verify_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age_seconds: int = INITDATA_MAX_AGE_SECONDS,
) -> WebAppInitData | None:
    """Validate the raw ``initData`` query string and return a typed wrapper.

    Signature + freshness only. It says nothing about whether this exact string
    has already been spent — the login path pairs it with
    ``app.core.initdata_replay.claim_initdata`` for that (MUDD-76).
    """
    if not init_data:
        return None

    parsed: dict[str, str] = dict(parse_qsl(init_data, strict_parsing=False, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        return None

    auth_date = int(parsed.get("auth_date", "0"))
    if auth_date <= 0 or time.time() - auth_date > max_age_seconds:
        return None

    user_raw = parsed.get("user")
    if not user_raw:
        return None
    try:
        user_dict = json.loads(user_raw)
    except json.JSONDecodeError:
        return None

    return WebAppInitData(
        user=WebAppUser(**user_dict),
        auth_date=auth_date,
        hash=received_hash,
        query_id=parsed.get("query_id"),
        chat_type=parsed.get("chat_type"),
        chat_instance=parsed.get("chat_instance"),
        start_param=parsed.get("start_param"),
        raw=parsed,
    )
