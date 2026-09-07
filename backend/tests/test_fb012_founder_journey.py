"""The founder's journey, end to end, and the ways it must not be short-cut.

The original report: a person already registered with @MuddatoBot, a merchant
customer created with that person's phone, and an app that kept saying "Telegram
ulanmagan". Recording a verified phone was not enough to close it — a contact
arrives in a private chat with no shop attached, so something has to go and
connect the records that were already waiting.

The second half of this file is the other side of the same coin: until that
proof exists, holding an invitation link must buy its holder nothing at all.
"""
from __future__ import annotations

import asyncio
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

from app.models import Customer, Installment, Invitation, Tenant, User
from app.models.enums import CustomerStatus, InstallmentStatus
from app.services.identity_link import reconcile_all_tenants
from app.services.telegram_contact import record_verified_contact

UZ_A = "+998901112233"
UZ_B = "+998901112244"
FOREIGN = "+14155552671"


def _init_data(telegram_id: int, *, first_name: str = "Joiner") -> str:
    from app.config import settings
    fields = {
        "user": json.dumps({"id": telegram_id, "first_name": first_name}),
        "auth_date": str(int(time.time())), "query_id": "AAH",
    }
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()})


@pytest_asyncio.fixture
async def api(migrated_db):
    import httpx
    from httpx import ASGITransport

    from app.db import get_db
    from app.main import app

    maker = async_sessionmaker(migrated_db, expire_on_commit=False)

    async def _override():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _override
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c, maker() as s:
        yield c, s
    app.dependency_overrides.pop(get_db, None)


async def _tenant(sess, prefix="shop") -> Tenant:
    t = Tenant(name=prefix, slug=f"{prefix}-{uuid.uuid4().hex[:8]}")
    sess.add(t)
    await sess.flush()
    return t


async def _customer(sess, tenant, phone, **kw) -> Customer:
    c = Customer(tenant_id=tenant.id, full_name="Shop's own label", phone=phone, **kw)
    sess.add(c)
    await sess.flush()
    return c


async def _prove(sess, telegram_id: int, phone: str) -> User:
    u = (await sess.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
    if u is None:
        u = User(telegram_id=telegram_id, first_name="Muxammad")
        sess.add(u)
        await sess.flush()
    r = await record_verified_contact(
        sess, sender_telegram_id=telegram_id, contact_user_id=telegram_id, raw_phone=phone,
    )
    assert r.ok, r.outcome
    return u


# ── the journey ─────────────────────────────────────────────────────────────

class TestFounderJourney:
    async def test_verifying_connects_the_records_already_waiting(self, api):
        """The whole report, in one test.

        Registered with the bot; a shop typed the same number into its books;
        the app said not connected. Proving the number now goes and connects it.
        """
        client, sess = api
        tenant = await _tenant(sess)
        customer = await _customer(sess, tenant, UZ_A, status=CustomerStatus.pending)
        user = User(telegram_id=42001, first_name="Muddato Support")
        sess.add(user)
        await sess.commit()

        # Before: known to the bot, unknown to the shop.
        assert customer.telegram_id is None

        await _prove(sess, 42001, UZ_A)
        sweep = await reconcile_all_tenants(sess, user=user)
        await sess.commit()

        assert sweep.linked == 1
        await sess.refresh(customer)
        assert customer.telegram_id == 42001
        assert customer.status == CustomerStatus.active

        from app.core.security import issue_token
        auth = {"Authorization": f"Bearer {issue_token(user_id=str(user.id))}"}
        body = (await client.get("/api/me/identity", headers=auth)).json()
        assert body["state"] == "VERIFIED"
        assert body["linkedShops"] == 1

    async def test_a_second_contact_delivery_changes_nothing(self, api):
        _client, sess = api
        tenant = await _tenant(sess)
        customer = await _customer(sess, tenant, UZ_A)
        user = await _prove(sess, 42002, UZ_A)
        await reconcile_all_tenants(sess, user=user)
        await sess.commit()

        again = await record_verified_contact(
            sess, sender_telegram_id=42002, contact_user_id=42002, raw_phone=UZ_A,
        )
        second = await reconcile_all_tenants(sess, user=user)
        await sess.commit()
        assert again.ok
        assert second.linked == 0 and second.already == 1
        await sess.refresh(customer)
        assert customer.telegram_id == 42002

    async def test_the_sweep_links_every_shop_the_person_deals_with(self, api):
        _client, sess = api
        a, b = await _tenant(sess, "a"), await _tenant(sess, "b")
        ca, cb = await _customer(sess, a, UZ_A), await _customer(sess, b, UZ_A)
        user = await _prove(sess, 42003, UZ_A)
        sweep = await reconcile_all_tenants(sess, user=user)
        await sess.commit()
        assert sweep.linked == 2
        await sess.refresh(ca); await sess.refresh(cb)
        assert ca.telegram_id == cb.telegram_id == 42003

    async def test_one_ambiguous_shop_does_not_cost_another_its_link(self, api, monkeypatch):
        """Savepoint per tenant. An ambiguity in one shop must not roll back a
        clean link in another — nor the phone proof that triggered the sweep."""
        import app.services.identity_link as link_mod
        _client, sess = api
        good, messy = await _tenant(sess, "good"), await _tenant(sess, "messy")
        clean = await _customer(sess, good, UZ_A)
        one = await _customer(sess, messy, UZ_A)
        user = await _prove(sess, 42004, UZ_A)

        real = link_mod.find_candidates

        async def _ambiguous_in_messy(db, *, tenant_id, e164):
            if tenant_id == messy.id:
                return [one, one]      # two rows, as legacy data can be
            return await real(db, tenant_id=tenant_id, e164=e164)
        monkeypatch.setattr(link_mod, "find_candidates", _ambiguous_in_messy)

        sweep = await reconcile_all_tenants(sess, user=user)
        await sess.commit()

        assert sweep.linked == 1 and sweep.needs_operator == 1
        await sess.refresh(clean); await sess.refresh(one); await sess.refresh(user)
        assert clean.telegram_id == 42004          # the good shop still linked
        assert one.telegram_id is None             # the messy one untouched
        assert user.verified_phone == UZ_A         # the proof survived

    async def test_a_failed_tenant_cannot_discard_the_whole_transaction(self, api, monkeypatch):
        """The savepoint's real job.

        `reconcile` rolls back on an IntegrityError — the concurrent-binding
        case. Without a savepoint that rollback discards the ENTIRE transaction:
        every other tenant's link, and the phone proof that started the sweep.
        """
        import app.services.identity_link as link_mod
        _client, sess = api
        good, broken = await _tenant(sess, "good"), await _tenant(sess, "broken")
        clean = await _customer(sess, good, UZ_A)
        doomed = await _customer(sess, broken, UZ_A)
        user = await _prove(sess, 42010, UZ_A)
        await sess.commit()

        real = link_mod.reconcile

        async def _explode_in_broken(db, *, user, tenant_id, expected_customer_id=None):
            if tenant_id == broken.id:
                # Exactly what the IntegrityError branch does: a failed write
                # inside its own savepoint.
                from sqlalchemy.exc import IntegrityError
                try:
                    async with db.begin_nested():
                        raise IntegrityError("forced", None, Exception("clash"))
                except IntegrityError:
                    pass
                return link_mod.LinkResult(link_mod.LinkState.identity_taken)
            return await real(db, user=user, tenant_id=tenant_id,
                              expected_customer_id=expected_customer_id)
        monkeypatch.setattr(link_mod, "reconcile", _explode_in_broken)

        sweep = await link_mod.reconcile_all_tenants(sess, user=user)
        await sess.commit()

        assert sweep.needs_operator == 1
        # Re-read rather than refresh: a rollback detaches the instances, and
        # what matters is what the DATABASE kept.
        fresh_clean = (await sess.execute(
            select(Customer).where(Customer.id == clean.id))).scalar_one()
        fresh_doomed = (await sess.execute(
            select(Customer).where(Customer.id == doomed.id))).scalar_one()
        fresh_user = (await sess.execute(
            select(User).where(User.telegram_id == 42010))).scalar_one()
        assert fresh_clean.telegram_id == 42010   # the healthy tenant kept its link
        assert fresh_doomed.telegram_id is None
        assert fresh_user.verified_phone == UZ_A  # and the proof survived

    async def test_a_tenant_left_for_an_operator_leaves_nothing_behind(self, api, monkeypatch):
        """The per-tenant savepoint's own job.

        A tenant can do work before its ambiguity is discovered. Whatever it
        wrote must be discarded when the decision is "leave this one alone" —
        otherwise a half-applied link survives in a shop nobody reconciled.
        """
        import app.services.identity_link as link_mod
        _client, sess = api
        good, messy = await _tenant(sess, "good2"), await _tenant(sess, "messy2")
        clean = await _customer(sess, good, UZ_A)
        half = await _customer(sess, messy, UZ_A)
        user = await _prove(sess, 42011, UZ_A)
        await sess.commit()
        half_id, clean_id = half.id, clean.id

        real = link_mod.reconcile

        async def _writes_then_gives_up(db, *, user, tenant_id, expected_customer_id=None):
            if tenant_id == messy.id:
                row = (await db.execute(
                    select(Customer).where(Customer.id == half_id))).scalar_one()
                row.telegram_id = user.telegram_id       # partial work…
                await db.flush()
                return link_mod.LinkResult(link_mod.LinkState.ambiguous, None, 2)
            return await real(db, user=user, tenant_id=tenant_id,
                              expected_customer_id=expected_customer_id)
        monkeypatch.setattr(link_mod, "reconcile", _writes_then_gives_up)

        sweep = await link_mod.reconcile_all_tenants(sess, user=user)
        await sess.commit()

        assert sweep.linked == 1 and sweep.needs_operator == 1
        fresh_half = (await sess.execute(
            select(Customer).where(Customer.id == half_id))).scalar_one()
        fresh_clean = (await sess.execute(
            select(Customer).where(Customer.id == clean_id))).scalar_one()
        assert fresh_half.telegram_id is None, "…must be discarded with the savepoint"
        assert fresh_clean.telegram_id == 42011

    async def test_a_customer_created_after_verification_links_immediately(self, api):
        """The other direction: prove first, then a shop enters you. Nobody is
        asked to send their contact a second time."""
        from app.core.security import issue_token
        from app.models import TenantUser
        from app.models.enums import TenantRole

        client, sess = api
        tenant = await _tenant(sess)
        owner = User(telegram_id=42005, first_name="Owner")
        sess.add(owner)
        await sess.flush()
        sess.add(TenantUser(tenant_id=tenant.id, user_id=owner.id, role=TenantRole.owner))
        await _prove(sess, 42006, UZ_A)
        await sess.commit()

        r = await client.post(
            "/api/tenant/customers",
            headers={"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}",
                     "X-Tenant-Slug": tenant.slug},
            json={"full_name": "Example User", "phone": UZ_A},
        )
        assert r.status_code in (200, 201), r.text
        created = (await sess.execute(
            select(Customer).where(Customer.tenant_id == tenant.id, Customer.phone == UZ_A)
        )).scalar_one()
        assert created.telegram_id == 42006

    async def test_an_unknown_number_creates_an_ordinary_unlinked_customer(self, api):
        from app.core.security import issue_token
        from app.models import TenantUser
        from app.models.enums import TenantRole

        client, sess = api
        tenant = await _tenant(sess)
        owner = User(telegram_id=42007, first_name="Owner")
        sess.add(owner)
        await sess.flush()
        sess.add(TenantUser(tenant_id=tenant.id, user_id=owner.id, role=TenantRole.owner))
        await sess.commit()

        r = await client.post(
            "/api/tenant/customers",
            headers={"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}",
                     "X-Tenant-Slug": tenant.slug},
            json={"full_name": "Nobody Yet", "phone": UZ_B},
        )
        assert r.status_code in (200, 201), r.text
        created = (await sess.execute(
            select(Customer).where(Customer.tenant_id == tenant.id, Customer.phone == UZ_B)
        )).scalar_one()
        assert created.telegram_id is None

    async def test_a_foreign_proof_links_nothing(self, api):
        _client, sess = api
        tenant = await _tenant(sess)
        customer = await _customer(sess, tenant, UZ_A)
        user = await _prove(sess, 42008, FOREIGN)
        sweep = await reconcile_all_tenants(sess, user=user)
        await sess.commit()
        assert sweep.linked == 0
        await sess.refresh(customer)
        assert customer.telegram_id is None

    async def test_the_sweep_says_nothing_about_which_shops(self, api):
        """A bot reply may say "some records need support". It may not say whose."""
        _client, sess = api
        a, b = await _tenant(sess, "a"), await _tenant(sess, "b")
        await _customer(sess, a, UZ_A)
        await _customer(sess, b, UZ_A)
        user = await _prove(sess, 42009, UZ_A)
        sweep = await reconcile_all_tenants(sess, user=user)
        blob = repr(sweep)
        assert str(a.id) not in blob and str(b.id) not in blob
        assert "998" not in blob


# ── the invitation must buy nothing without proof ───────────────────────────

class TestUnverifiedClaimIsWorthless:
    async def _invite(self, sess, customer) -> Invitation:
        inv = Invitation(
            token_verifier=uuid.uuid4().hex, tenant_id=customer.tenant_id,
            kind="customer", customer_id=customer.id, preset={},
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        sess.add(inv)
        await sess.commit()
        return inv

    async def test_a_stolen_invite_to_an_active_customer_gains_nothing(self, api):
        """The exposure the review found: the claim used to bind an unverified
        account to an existing — possibly ACTIVE — customer, and every `/me`
        read keyed off that binding."""
        from app.core import invite_codes
        from app.core.security import issue_token

        client, sess = api
        tenant = await _tenant(sess)
        victim = await _customer(sess, tenant, UZ_A, status=CustomerStatus.active)
        sess.add(Installment(
            tenant_id=tenant.id, customer_id=victim.id, contract_number="MDD-1",
            total_amount=1000, principal=1000, monthly_amount=100, months=10, currency="UZS",
            status=InstallmentStatus.active, start_date=datetime.now(UTC).date(),
        ))
        raw = "STOLEN-" + uuid.uuid4().hex
        inv = Invitation(
            token_verifier=invite_codes.verifier(raw), tenant_id=tenant.id,
            kind="customer", customer_id=victim.id, preset={},
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        sess.add(inv)
        thief = User(telegram_id=43001, first_name="Thief")
        sess.add(thief)
        await sess.commit()

        r = await client.post(f"/api/inv/{raw}/claim", json={
            "init_data": _init_data(43001), "full_name": "Thief", "phone": UZ_A,
        })
        assert r.json()["ok"] is False
        assert r.json()["identity_state"] == "VERIFICATION_REQUIRED"

        await sess.refresh(victim); await sess.refresh(inv)
        assert victim.telegram_id is None          # no binding
        assert victim.status == CustomerStatus.active
        assert inv.used_at is None                 # not consumed

        auth = {"Authorization": f"Bearer {issue_token(user_id=str(thief.id))}"}
        assert (await client.get("/api/me/installments", headers=auth)).json() == []
        assert (await client.get("/api/me/identity", headers=auth)).json()["linkedShops"] == 0

    async def test_a_typed_matching_phone_does_not_link(self, api):
        from app.core import invite_codes
        client, sess = api
        tenant = await _tenant(sess)
        c = await _customer(sess, tenant, UZ_A, status=CustomerStatus.pending)
        raw = "RAW-" + uuid.uuid4().hex
        sess.add(Invitation(
            token_verifier=invite_codes.verifier(raw), tenant_id=tenant.id,
            kind="customer", customer_id=c.id, preset={},
            expires_at=datetime.now(UTC) + timedelta(days=1),
        ))
        sess.add(User(telegram_id=43002, first_name="Typer"))
        await sess.commit()

        r = await client.post(f"/api/inv/{raw}/claim", json={
            "init_data": _init_data(43002), "full_name": "Typer", "phone": UZ_A,
        })
        assert r.json()["ok"] is False
        await sess.refresh(c)
        assert c.telegram_id is None
        assert c.status == CustomerStatus.pending

    async def test_a_verified_matching_phone_links_activates_and_consumes_once(self, api):
        from app.core import invite_codes
        client, sess = api
        tenant = await _tenant(sess)
        c = await _customer(sess, tenant, UZ_A, status=CustomerStatus.pending)
        raw = "RAW-" + uuid.uuid4().hex
        inv = Invitation(
            token_verifier=invite_codes.verifier(raw), tenant_id=tenant.id,
            kind="customer", customer_id=c.id, preset={},
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        sess.add(inv)
        await _prove(sess, 43003, UZ_A)
        await sess.commit()

        r = await client.post(f"/api/inv/{raw}/claim", json={
            "init_data": _init_data(43003), "full_name": "Muxammad",
        })
        assert r.status_code == 200 and r.json()["ok"] is True
        assert r.json()["identity_state"] == "VERIFIED"
        await sess.refresh(c); await sess.refresh(inv)
        assert c.telegram_id == 43003
        assert c.status == CustomerStatus.active
        assert inv.used_at is not None

        second = await client.post(f"/api/inv/{raw}/claim", json={
            "init_data": _init_data(43003), "full_name": "Muxammad",
        })
        assert second.status_code == 410

    async def test_concurrent_verified_claims_succeed_at_most_once(self, api):
        from app.core import invite_codes
        client, sess = api
        tenant = await _tenant(sess)
        c = await _customer(sess, tenant, UZ_A, status=CustomerStatus.pending)
        raw = "RAW-" + uuid.uuid4().hex
        sess.add(Invitation(
            token_verifier=invite_codes.verifier(raw), tenant_id=tenant.id,
            kind="customer", customer_id=c.id, preset={},
            expires_at=datetime.now(UTC) + timedelta(days=1),
        ))
        await _prove(sess, 43004, UZ_A)
        await sess.commit()

        body = {"init_data": _init_data(43004), "full_name": "Muxammad"}
        out = await asyncio.gather(
            client.post(f"/api/inv/{raw}/claim", json=body),
            client.post(f"/api/inv/{raw}/claim", json=body),
            return_exceptions=True,
        )
        oks = [r for r in out if not isinstance(r, BaseException)
               and r.status_code == 200 and r.json().get("ok")]
        assert len(oks) <= 1

    async def test_a_legacy_link_without_proof_exposes_nothing(self, api):
        """Bindings created before proof existed are pointers, not passes."""
        from app.core.security import issue_token
        client, sess = api
        tenant = await _tenant(sess)
        c = await _customer(sess, tenant, UZ_A, telegram_id=43005,
                            status=CustomerStatus.active)
        sess.add(Installment(
            tenant_id=tenant.id, customer_id=c.id, contract_number="MDD-2",
            total_amount=500, principal=500, monthly_amount=50, months=10, currency="UZS",
            status=InstallmentStatus.active, start_date=datetime.now(UTC).date(),
        ))
        legacy = User(telegram_id=43005, first_name="Legacy")   # no verified phone
        sess.add(legacy)
        await sess.commit()

        auth = {"Authorization": f"Bearer {issue_token(user_id=str(legacy.id))}"}
        assert (await client.get("/api/me/installments", headers=auth)).json() == []
        assert (await client.get("/api/me/summary", headers=auth)).json()["overdue_count"] == 0
        assert (await client.get("/api/me/identity", headers=auth)).json()["linkedShops"] == 0

        # …and the same account, once proved, sees exactly its own shop.
        await _prove(sess, 43005, UZ_A)
        await sess.commit()
        rows = (await client.get("/api/me/installments", headers=auth)).json()
        assert len(rows) == 1

    async def test_a_verified_foreign_account_gets_no_access(self, api):
        from app.core.security import issue_token
        client, sess = api
        tenant = await _tenant(sess)
        await _customer(sess, tenant, UZ_A, telegram_id=43006)
        user = await _prove(sess, 43006, FOREIGN)
        await sess.commit()
        auth = {"Authorization": f"Bearer {issue_token(user_id=str(user.id))}"}
        assert (await client.get("/api/me/installments", headers=auth)).json() == []

    async def test_a_contract_pdf_needs_the_same_proof(self, api):
        from app.core.security import issue_token
        client, sess = api
        tenant = await _tenant(sess)
        c = await _customer(sess, tenant, UZ_A, telegram_id=43007)
        ins = Installment(
            tenant_id=tenant.id, customer_id=c.id, contract_number="MDD-3",
            total_amount=500, principal=500, monthly_amount=50, months=10, currency="UZS",
            status=InstallmentStatus.active, start_date=datetime.now(UTC).date(),
        )
        sess.add(ins)
        unproved = User(telegram_id=43007, first_name="Unproved")
        sess.add(unproved)
        await sess.commit()
        auth = {"Authorization": f"Bearer {issue_token(user_id=str(unproved.id))}"}
        # The real route. An earlier draft of this test used a path that does
        # not exist, so it passed for the wrong reason — 404 because nothing was
        # routed, not because access was refused.
        r = await client.get(
            f"/api/tenant/installments/{ins.id}/contract.pdf", headers=auth,
        )
        # A uniform 404, so the refusal is not an existence oracle either.
        assert r.status_code == 404, f"{r.status_code} {r.text[:120]}"

        # …and the same account, once proved, is served.
        await _prove(sess, 43007, UZ_A)
        await sess.commit()
        ok = await client.get(
            f"/api/tenant/installments/{ins.id}/contract.pdf", headers=auth,
        )
        assert ok.status_code != 404, "the gate, not the route, was doing the refusing"


def test_only_one_module_rebinds_an_existing_customer():
    """Two places deciding which customer an identity belongs to is two places
    to get it wrong — and rebinding an EXISTING row is the dangerous shape, the
    one an unverified claim used to perform."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    writers = set()
    # `<something>.telegram_id = …` on an already-loaded row. A constructor
    # argument (`Customer(telegram_id=…)`) creates a row and is not this.
    pattern = re.compile(r"^\s*(?!#)\w+\.telegram_id\s*=", re.M)
    for path in root.rglob("*.py"):
        if pattern.search(path.read_text()):
            writers.add(path.relative_to(root).as_posix())
    # `services/privacy.py` clears the link during an erasure; that is
    # unbinding, not deciding who somebody is.
    assert writers == {
        "services/identity_link.py", "api/invitations.py", "services/privacy.py",
    }, writers
    # The guarantor branch is the one in invitations.py, and it is unreachable
    # without an eligible proof.
    src = (root / "api/invitations.py").read_text()
    gate = 'if inv.kind in {"customer", "guarantor"} and not eligible_proof:'
    assert gate in src
    assert src.index(gate) < src.index("g.telegram_id = u.id")


@pytest.mark.parametrize("banned", ["FB-01", "Batch 3", "Phase 9"])
def test_no_audit_narrative_in_touched_source(banned):
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("app/services/identity_link.py", "app/api/invitations.py",
                 "app/api/me.py", "app/api/pdf.py", "app/core/invite_codes.py",
                 "app/api/tenant/customers.py"):
        assert banned not in (root / name).read_text(), name
