"""FB-012 / FB-013 at the HTTP edge — the invitation must not ask twice.

The join form asked every arrival for a name and a phone, including people whose
number Telegram had already proved and whose record the shop had already filled
in. These tests drive the two public endpoints end to end: what the server will
tell a joiner before the form renders, and what it accepts when they submit.

They also cover the attacks that shape those endpoints — a forged initData, a
phone posted straight into the JSON, a token used twice, and a probe for
somebody else's customer.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Customer, Invitation, Tenant, User
from app.models.enums import CustomerStatus

UZ_A = "+998901112233"
UZ_B = "+998901112244"


def _init_data(telegram_id: int, *, first_name: str = "Joiner", auth_date: int | None = None) -> str:
    from app.config import settings
    fields = {
        "user": json.dumps({"id": telegram_id, "first_name": first_name}),
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAH",
    }
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()})


@pytest_asyncio.fixture
async def api(migrated_db):
    """Client + session sharing one engine, so a row written here is visible
    to the endpoint under test."""
    import httpx
    from httpx import ASGITransport

    from app.db import get_db
    from app.main import app

    maker = async_sessionmaker(migrated_db, expire_on_commit=False)

    async def _override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _override
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client, maker() as sess:
        yield client, sess
    app.dependency_overrides.pop(get_db, None)


async def _seed(sess, *, phone=UZ_A, telegram_id=None, status=CustomerStatus.pending):
    t = Tenant(name="Shop", slug=f"s-{uuid.uuid4().hex[:8]}")
    sess.add(t)
    await sess.flush()
    c = Customer(tenant_id=t.id, full_name="Shop's own label", phone=phone,
                 telegram_id=telegram_id, status=status)
    sess.add(c)
    await sess.flush()
    inv = Invitation(
        token=uuid.uuid4().hex[:10], tenant_id=t.id, kind="customer",
        customer_id=c.id, preset={}, expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    sess.add(inv)
    await sess.commit()
    return t, c, inv


async def _verify(sess, telegram_id: int, phone: str) -> User:
    from app.services.telegram_contact import record_verified_contact
    u = User(telegram_id=telegram_id, first_name="Joiner")
    sess.add(u)
    await sess.flush()
    r = await record_verified_contact(
        sess, sender_telegram_id=telegram_id, contact_user_id=telegram_id, raw_phone=phone,
    )
    assert r.ok, r.outcome
    await sess.commit()
    return u


# ── what the server tells the form before it renders ────────────────────────

class TestPrefill:
    async def test_a_proved_number_comes_back_masked_and_matching(self, api):
        client, sess = api
        _t, _c, inv = await _seed(sess)
        await _verify(sess, 5001, UZ_A)
        r = await client.post(f"/api/inv/{inv.token}/identity",
                              json={"init_data": _init_data(5001)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "VERIFIED"
        assert body["verified"] is True
        assert body["matchesInvitation"] is True
        # Masked, even to its owner: this response lands in a WebView console.
        assert body["phoneMasked"].startswith("+998")
        assert "901112233" not in json.dumps(body)

    async def test_an_unproved_visitor_is_asked_to_verify(self, api):
        client, sess = api
        _t, _c, inv = await _seed(sess)
        r = await client.post(f"/api/inv/{inv.token}/identity",
                              json={"init_data": _init_data(5002)})
        assert r.json()["state"] == "VERIFICATION_REQUIRED"
        assert r.json()["phoneMasked"] is None

    async def test_a_proved_number_for_the_wrong_invitation_is_a_state(self, api):
        client, sess = api
        _t, _c, inv = await _seed(sess, phone=UZ_B)
        await _verify(sess, 5003, UZ_A)
        body = (await client.post(f"/api/inv/{inv.token}/identity",
                                  json={"init_data": _init_data(5003)})).json()
        assert body["state"] == "PHONE_DOES_NOT_MATCH_INVITATION"
        assert body["matchesInvitation"] is False

    async def test_a_foreign_number_is_told_the_region_is_unsupported(self, api):
        client, sess = api
        _t, _c, inv = await _seed(sess)
        await _verify(sess, 5004, "+14155552671")
        body = (await client.post(f"/api/inv/{inv.token}/identity",
                                  json={"init_data": _init_data(5004)})).json()
        assert body["state"] == "UNSUPPORTED_REGION"
        assert body["verified"] is True          # proved, just not eligible

    async def test_the_prefill_never_describes_the_invited_customer(self, api):
        """It answers about the CALLER. A token holder learns nothing about the
        record behind it beyond whether their own number is the one expected."""
        client, sess = api
        _t, c, inv = await _seed(sess)
        body = (await client.post(f"/api/inv/{inv.token}/identity",
                                  json={"init_data": _init_data(5005)})).json()
        blob = json.dumps(body)
        assert c.full_name not in blob
        assert str(c.id) not in blob
        assert "901112233" not in blob

    async def test_a_forged_init_data_learns_nothing(self, api):
        client, sess = api
        _t, _c, inv = await _seed(sess)
        for forged in ("", "user=%7B%22id%22%3A1%7D&hash=deadbeef" + "0" * 26,
                       _init_data(5006) + "tampered"):
            r = await client.post(f"/api/inv/{inv.token}/identity", json={"init_data": forged})
            assert r.status_code == 401, forged[:40]

    async def test_an_expired_init_data_is_refused(self, api):
        client, sess = api
        _t, _c, inv = await _seed(sess)
        stale = _init_data(5007, auth_date=int(time.time()) - 90_000)
        r = await client.post(f"/api/inv/{inv.token}/identity", json={"init_data": stale})
        assert r.status_code == 401

    async def test_an_unknown_token_is_a_flat_404(self, api):
        client, _sess = api
        r = await client.post("/api/inv/zzzzzzzz/identity",
                              json={"init_data": _init_data(5008)})
        assert r.status_code == 404
        assert "tenant" not in r.text.lower()

    async def test_a_used_token_cannot_be_probed(self, api):
        client, sess = api
        _t, _c, inv = await _seed(sess)
        inv.used_at = datetime.now(UTC)
        await sess.commit()
        r = await client.post(f"/api/inv/{inv.token}/identity",
                              json={"init_data": _init_data(5009)})
        assert r.status_code == 410


# ── what the server accepts on submit ───────────────────────────────────────

class TestClaim:
    async def test_a_proved_number_links_and_activates(self, api):
        client, sess = api
        _t, c, inv = await _seed(sess)
        await _verify(sess, 6001, UZ_A)
        r = await client.post(f"/api/inv/{inv.token}/claim", json={
            "init_data": _init_data(6001), "full_name": "Whatever They Typed",
        })
        assert r.status_code == 200, r.text
        assert r.json()["identity_state"] == "VERIFIED"
        await sess.refresh(c)
        assert c.telegram_id == 6001
        assert c.status == CustomerStatus.active

    async def test_a_typed_phone_cannot_override_a_proved_one(self, api):
        """The attack this whole batch is shaped around: posting a number into
        the JSON must not make it the verified one."""
        client, sess = api
        _t, _c, inv = await _seed(sess)
        u = await _verify(sess, 6002, UZ_A)
        await client.post(f"/api/inv/{inv.token}/claim", json={
            "init_data": _init_data(6002), "full_name": "Ali Valiyev", "phone": UZ_B,
        })
        await sess.refresh(u)
        assert u.verified_phone == UZ_A

    async def test_a_mismatched_proof_does_not_bind_and_leaves_the_token_live(self, api):
        client, sess = api
        _t, c, inv = await _seed(sess, phone=UZ_B)
        await _verify(sess, 6003, UZ_A)
        r = await client.post(f"/api/inv/{inv.token}/claim", json={
            "init_data": _init_data(6003), "full_name": "Ali Valiyev",
        })
        assert r.json()["identity_state"] == "PHONE_DOES_NOT_MATCH_INVITATION"
        await sess.refresh(c)
        assert c.telegram_id is None
        await sess.refresh(inv)
        # Unused: the person can finish once the mismatch is sorted out.
        assert inv.used_at is None

    async def test_a_claim_without_proof_is_refused_whatever_it_carries(self, api):
        """A customer relationship carries contracts, so it is created only
        against proved ownership — with or without a typed number in the body."""
        client, sess = api
        _t, c, inv = await _seed(sess)
        for body in ({"init_data": _init_data(6004), "full_name": "Ali Valiyev"},
                     {"init_data": _init_data(6004), "full_name": "Ali Valiyev",
                      "phone": UZ_A}):
            r = await client.post(f"/api/inv/{inv.token}/claim", json=body)
            assert r.status_code == 200
            assert r.json()["ok"] is False
            assert r.json()["identity_state"] == "VERIFICATION_REQUIRED"
        await sess.refresh(c); await sess.refresh(inv)
        assert c.telegram_id is None
        assert inv.used_at is None

    async def test_an_unproved_claim_creates_no_link_at_all(self, api):
        """It used to bind the record and merely leave it pending — but every
        `/me` read keyed off that binding, so the link itself was the exposure."""
        client, sess = api
        _t, c, inv = await _seed(sess)
        r = await client.post(f"/api/inv/{inv.token}/claim", json={
            "init_data": _init_data(6005), "full_name": "Ali Valiyev", "phone": UZ_A,
        })
        assert r.json()["ok"] is False
        await sess.refresh(c)
        assert c.telegram_id is None
        assert c.status == CustomerStatus.pending

    async def test_a_token_is_single_use(self, api):
        client, sess = api
        _t, _c, inv = await _seed(sess)
        await _verify(sess, 6006, UZ_A)
        first = await client.post(f"/api/inv/{inv.token}/claim", json={
            "init_data": _init_data(6006), "full_name": "Ali Valiyev",
        })
        assert first.status_code == 200
        second = await client.post(f"/api/inv/{inv.token}/claim", json={
            "init_data": _init_data(6006), "full_name": "Ali Valiyev",
        })
        assert second.status_code == 410

    async def test_concurrent_redemption_binds_once(self, api):
        """Two taps, one flaky connection, one link."""
        import asyncio
        client, sess = api
        _t, c, inv = await _seed(sess)
        await _verify(sess, 6007, UZ_A)
        body = {"init_data": _init_data(6007), "full_name": "Ali Valiyev"}
        results = await asyncio.gather(
            client.post(f"/api/inv/{inv.token}/claim", json=body),
            client.post(f"/api/inv/{inv.token}/claim", json=body),
            return_exceptions=True,
        )
        codes = sorted(r.status_code for r in results if not isinstance(r, BaseException))
        assert codes.count(200) <= 1
        await sess.refresh(c)
        assert c.telegram_id == 6007

    async def test_a_second_telegram_account_cannot_take_a_linked_customer(self, api):
        client, sess = api
        _t, c, _inv = await _seed(sess, telegram_id=777001, status=CustomerStatus.active)
        inv2 = Invitation(
            token=uuid.uuid4().hex[:10], tenant_id=c.tenant_id, kind="customer",
            customer_id=c.id, preset={}, expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        sess.add(inv2)
        await sess.commit()
        await _verify(sess, 6008, UZ_A)
        r = await client.post(f"/api/inv/{inv2.token}/claim", json={
            "init_data": _init_data(6008), "full_name": "Impostor",
        })
        assert r.json()["identity_state"] in {
            "CUSTOMER_LINKED_TO_OTHER_IDENTITY", "PHONE_DOES_NOT_MATCH_INVITATION",
        }
        await sess.refresh(c)
        assert c.telegram_id == 777001

    async def test_an_unknown_customer_is_indistinguishable_from_another_tenants(self, api):
        """Two shapes of "not here" must answer the same way."""
        client, sess = api
        other = Tenant(name="Other", slug=f"o-{uuid.uuid4().hex[:8]}")
        sess.add(other)
        await sess.flush()
        sess.add(Customer(tenant_id=other.id, full_name="Someone else", phone=UZ_A))
        _t, _c, inv = await _seed(sess, phone=UZ_B)
        await sess.commit()
        await _verify(sess, 6009, UZ_A)
        r = await client.post(f"/api/inv/{inv.token}/claim", json={
            "init_data": _init_data(6009), "full_name": "Ali Valiyev",
        })
        assert r.json()["identity_state"] == "PHONE_DOES_NOT_MATCH_INVITATION"
        assert "Someone else" not in r.text
        assert str(other.id) not in r.text


# ── the authenticated identity surface ──────────────────────────────────────

class TestMeIdentity:
    async def test_it_reports_the_servers_view_masked(self, api):
        from app.core.security import issue_token
        client, sess = api
        u = await _verify(sess, 7001, UZ_A)
        r = await client.get("/api/me/identity",
                             headers={"Authorization": f"Bearer {issue_token(user_id=str(u.id))}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "VERIFIED"
        assert body["source"] == "telegram_contact"
        assert "901112233" not in json.dumps(body)

    async def test_it_needs_a_session(self, api):
        client, _sess = api
        assert (await client.get("/api/me/identity")).status_code in (401, 403)

    async def test_an_unverified_person_is_told_to_verify(self, api):
        from app.core.security import issue_token
        client, sess = api
        u = User(telegram_id=7002, first_name="Nobody", phone=UZ_B)   # typed only
        sess.add(u)
        await sess.commit()
        r = await client.get("/api/me/identity",
                             headers={"Authorization": f"Bearer {issue_token(user_id=str(u.id))}"})
        assert r.json()["state"] == "VERIFICATION_REQUIRED"
        assert r.json()["verified"] is False


async def test_a_linked_customer_survives_its_history(api):
    """Linking an identity must not disturb what the shop already recorded."""
    client, sess = api
    _t, c, inv = await _seed(sess)
    c.notes = "Paid late in March"
    await sess.commit()
    await _verify(sess, 8001, UZ_A)
    await client.post(f"/api/inv/{inv.token}/claim", json={
        "init_data": _init_data(8001), "full_name": "Renamed By Joiner",
    })
    fresh = (await sess.execute(select(Customer).where(Customer.id == c.id))).scalar_one()
    assert fresh.notes == "Paid late in March"
    # The shop's own label is the shop's; a joiner's typing does not rewrite it.
    assert fresh.full_name == "Shop's own label"


@pytest.mark.parametrize("banned", ["FB-01", "Batch 3", "Phase 9"])
def test_no_audit_narrative_in_the_touched_production_source(banned):
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("app/api/invitations.py", "app/api/me.py", "app/bot/main.py",
                 "app/models/user.py", "app/models/customer.py"):
        assert banned not in (root / name).read_text(), name
