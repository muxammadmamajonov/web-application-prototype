"""MUDD-76 — a Mini-App ``initData`` may be exchanged for a session exactly once.

The signature check alone (`verify_init_data`) says the string was signed by the
bot and is inside its freshness window. It does NOT say the string is being
presented for the first time, so before this the same captured blob logged an
attacker in as its owner for the whole window — bearer-equivalent, 24h wide.

These tests pin the three properties that close it:

* a replayed initData is refused (**first** exchange must still succeed, or the
  test would pass on a route that is simply broken);
* the refusal is a *distinct* code, so the client can tell "already spent" from
  "invalid" and not throw away the session the first exchange established;
* an unreachable seen-store fails **closed** (503), never silently back to the
  old accept-everything behaviour.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings

BOT = "424242:MUDD76-TEST-TOKEN"


def _initdata(*, bot_token: str = BOT, user_id: int = 776, auth_date: int | None = None) -> str:
    """A genuinely signed initData string — the same shape Telegram emits."""
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "user": json.dumps({"id": user_id, "first_name": "R"}),
        "query_id": "AAH",
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class _FakeRedis:
    """Just enough Redis: ``SET NX EX`` semantics, plus the limiter's ``eval``."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False):
        if nx and key in self.store:
            return None          # redis-py returns None when NX declines the write
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def eval(self, *_a: object, **_k: object) -> int:
        return 1                 # rate limiter: first hit in the window


class _DeadRedis:
    async def set(self, *_a: object, **_k: object):
        raise ConnectionError("redis is down")


@pytest.fixture
def webapp_client(monkeypatch: pytest.MonkeyPatch):
    """ASGI client for POST /api/auth/telegram/webapp, DB- and Redis-free.

    The account write is stubbed so the test stays in the unit tier: what is
    under test is the credential gate ahead of it, and that gate is reached with
    a REAL signature over a REAL initData string — only the persistence behind it
    is faked.
    """
    from app.main import app

    monkeypatch.setattr(settings, "telegram_bot_token", BOT, raising=False)

    fake = _FakeRedis()
    monkeypatch.setattr("app.core.ratelimit.get_redis", lambda: fake)
    monkeypatch.setattr("app.core.initdata_replay.get_redis", lambda: fake)

    async def _stub_upsert(_db, **_kw):
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr("app.api.auth._upsert_user", _stub_upsert)

    async def _no_db():
        yield None

    from app.db import get_db

    app.dependency_overrides[get_db] = _no_db
    try:
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://t"), fake
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_replayed_initdata_is_refused(webapp_client) -> None:
    """The regression the issue is about: same string twice → 200 then 401."""
    client, _ = webapp_client
    init = _initdata()

    async with client:
        first = await client.post("/api/auth/telegram/webapp", json={"init_data": init})
        # A failing FIRST call would make the second one's 401 meaningless.
        assert first.status_code == 200, first.text
        assert first.json()["token"]

        second = await client.post("/api/auth/telegram/webapp", json={"init_data": init})

    assert second.status_code == 401
    assert second.json()["detail"] == "telegram_initdata_replayed"


async def test_replay_code_is_distinct_from_invalid(webapp_client) -> None:
    """`replayed` ≠ `invalid`.

    The client keeps the session it already holds on a replay (a reload inside
    one Mini-App launch re-sends the same string) but drops it on an invalid
    credential. Collapsing the two codes would log legitimate users out of their
    own reload — or, worse, invite someone to "fix" that by accepting replays.
    """
    client, _ = webapp_client
    async with client:
        forged = await client.post(
            "/api/auth/telegram/webapp",
            json={"init_data": _initdata(bot_token="999:not-our-token")},
        )
    assert forged.status_code == 401
    assert forged.json()["detail"] == "telegram_initdata_invalid"


async def test_a_second_distinct_initdata_still_logs_in(webapp_client) -> None:
    """Single-use is per credential, not a one-login-per-user latch."""
    client, _ = webapp_client
    async with client:
        a = await client.post("/api/auth/telegram/webapp", json={"init_data": _initdata(user_id=1)})
        b = await client.post("/api/auth/telegram/webapp", json={"init_data": _initdata(user_id=2)})
    assert (a.status_code, b.status_code) == (200, 200)


async def test_seen_store_outage_fails_closed(webapp_client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis down → 503, never a silent fall-back to accepting replays.

    Fail-open here would hand the whole replay window back to anyone able to
    degrade Redis, which is the one attacker most likely to be trying.
    """
    client, _ = webapp_client
    monkeypatch.setattr("app.core.initdata_replay.get_redis", lambda: _DeadRedis())

    async with client:
        r = await client.post("/api/auth/telegram/webapp", json={"init_data": _initdata()})

    assert r.status_code == 503
    assert r.json()["detail"] == "auth_store_unavailable"


async def test_expired_initdata_still_rejected_before_the_claim(webapp_client) -> None:
    """The window did not go away — single-use is layered on top of it."""
    client, fake = webapp_client
    stale = _initdata(auth_date=int(time.time()) - (settings.telegram_initdata_max_age_seconds + 60))

    async with client:
        r = await client.post("/api/auth/telegram/webapp", json={"init_data": stale})

    assert r.status_code == 401
    assert r.json()["detail"] == "telegram_initdata_invalid"
    # An unverifiable string must never reach the store — otherwise anyone could
    # fill it with junk keys, and a lucky guess would burn a real credential.
    assert not [k for k in fake.store if k.startswith("tg:initdata:used")]


# ── the store itself ────────────────────────────────────────────────────────

async def test_claim_is_purpose_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    """One initData presented for two different purposes is not a replay.

    The invitation flow legitimately shows the SAME string to `preview` and then
    to `claim`; a single global keyspace would read that normal two-step as an
    attack.
    """
    from app.core import initdata_replay

    monkeypatch.setattr(initdata_replay, "get_redis", lambda: _FakeRedis())
    assert await initdata_replay.claim_initdata("h", purpose="webapp_login", ttl_seconds=60)
    assert await initdata_replay.claim_initdata("h", purpose="invite_claim", ttl_seconds=60)


async def test_claim_never_stores_the_raw_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inside its window the hash still authenticates its bearer — so a Redis
    dump (or an eyeball on a key listing) must not yield a usable one."""
    from app.core import initdata_replay

    fake = _FakeRedis()
    monkeypatch.setattr(initdata_replay, "get_redis", lambda: fake)
    await initdata_replay.claim_initdata("super-secret-hash", purpose="webapp_login", ttl_seconds=60)

    assert fake.store
    assert not any("super-secret-hash" in k for k in fake.store)


async def test_claim_ttl_covers_the_acceptance_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key that expires before the initData does would reopen the window."""
    from app.core import initdata_replay

    fake = _FakeRedis()
    monkeypatch.setattr(initdata_replay, "get_redis", lambda: fake)
    await initdata_replay.claim_initdata("h", purpose="webapp_login", ttl_seconds=1234)

    assert set(fake.ttls.values()) == {1234}


async def test_store_outage_raises_rather_than_returning_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper must not have a truthy 'couldn't check' return value — that is
    how a fail-closed call site accidentally becomes fail-open."""
    from app.core import initdata_replay

    monkeypatch.setattr(initdata_replay, "get_redis", lambda: _DeadRedis())
    with pytest.raises(initdata_replay.ReplayStoreUnavailable):
        await initdata_replay.claim_initdata("h", purpose="webapp_login", ttl_seconds=60)


def test_window_default_is_an_hour_and_config_agrees() -> None:
    """86400 → 3600 (MUDD-76 item 2), and the two declarations stay in step.

    `telegram_auth` is deliberately settings-free (pure crypto, imported without
    an environment), so the number exists in both places; this is the pin.
    """
    from app.core.telegram_auth import INITDATA_MAX_AGE_SECONDS

    assert INITDATA_MAX_AGE_SECONDS == 3_600
    assert type(settings).model_fields["telegram_initdata_max_age_seconds"].default == 3_600
