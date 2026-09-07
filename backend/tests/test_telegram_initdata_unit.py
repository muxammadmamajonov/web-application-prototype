"""Unit-level security matrix for Telegram Mini-App initData validation.

Fast, DB-free coverage of `verify_init_data` — the gate that turns the
client-supplied `window.Telegram.WebApp.initData` into a trusted identity. The
endpoint-level happy path + tenant-bot fallback live in
test_telegram_webapp_auth.py; here we pin the negative cases the threat model
cares about: tampered hash, mutated payload, expired auth_date, wrong bot token,
and missing fields. A regression in any of these would let a forged user in.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, urlencode

from app.core.telegram_auth import verify_init_data

BOT = "123456:UNIT-TEST-TOKEN"


def _sign(fields: dict[str, str], *, token: str = BOT) -> str:
    """Produce a correctly-signed initData query string for `fields`."""
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signed = dict(fields)
    signed["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(signed)


def _fields(**over: str) -> dict[str, str]:
    base = {
        "user": json.dumps({"id": 42, "first_name": "T", "username": "t"}),
        "auth_date": str(int(time.time())),
        "query_id": "AAH",
    }
    base.update(over)
    return base


def test_valid_initdata_returns_typed_user() -> None:
    res = verify_init_data(_sign(_fields()), bot_token=BOT)
    assert res is not None
    assert res.user.id == 42
    assert res.user.username == "t"


def test_tampered_hash_is_rejected() -> None:
    signed = _sign(_fields())
    flipped = signed[:-1] + ("0" if signed[-1] != "0" else "1")
    assert verify_init_data(flipped, bot_token=BOT) is None


def test_payload_mutated_after_signing_is_rejected() -> None:
    """Re-attach a valid hash to a swapped user — the classic forgery attempt."""
    d = dict(parse_qsl(_sign(_fields())))
    d["user"] = json.dumps({"id": 999999, "first_name": "Evil"})  # mutate, keep old hash
    assert verify_init_data(urlencode(d), bot_token=BOT) is None


def test_expired_auth_date_is_rejected() -> None:
    old = str(int(time.time()) - 7 * 24 * 3600)  # a week ago
    assert verify_init_data(_sign(_fields(auth_date=old)), bot_token=BOT, max_age_seconds=86_400) is None


def test_wrong_bot_token_is_rejected() -> None:
    signed = _sign(_fields(), token="999:OTHER-BOT")
    assert verify_init_data(signed, bot_token=BOT) is None


def test_missing_hash_is_rejected() -> None:
    f = _fields()
    assert verify_init_data(urlencode(f), bot_token=BOT) is None  # never signed → no hash


def test_missing_user_is_rejected_even_with_valid_hash() -> None:
    f = {"auth_date": str(int(time.time())), "query_id": "AAH"}  # no user field
    assert verify_init_data(_sign(f), bot_token=BOT) is None


def test_empty_and_garbage_are_rejected() -> None:
    assert verify_init_data("", bot_token=BOT) is None
    assert verify_init_data("not-a-query-string", bot_token=BOT) is None
