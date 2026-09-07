"""Who may be told about a customer, and who may be handed an invitation.

The identity service used to answer "is this number known?" from `users.phone` —
the number a person typed into their OWN profile. Nothing had been proved, so
typing a stranger's number made you, as far as that lookup was concerned, the
account behind it: you were "known", you were the notification recipient, and a
shop's invitation was delivered to you.

The other half is the opposite waste: a customer who was just linked to a proved
identity was still handed a one-time invitation to join something they are
already in.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Customer, Invitation, Tenant, TenantUser, User
from app.models.enums import TenantRole
from app.services import customer_identity
from app.services.identity_link import find_verified_owner, find_verified_owners
from app.services.telegram_contact import record_verified_contact

UZ_A = "+998901112233"
UZ_B = "+998901112244"
FOREIGN = "+14155552671"


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


async def _owner(sess, tenant, telegram_id: int) -> User:
    u = User(telegram_id=telegram_id, first_name="Owner")
    sess.add(u)
    await sess.flush()
    sess.add(TenantUser(tenant_id=tenant.id, user_id=u.id, role=TenantRole.owner))
    await sess.flush()
    return u


async def _prove(sess, telegram_id: int, phone: str, **kw) -> User:
    u = User(telegram_id=telegram_id, first_name="Person", **kw)
    sess.add(u)
    await sess.flush()
    assert (await record_verified_contact(
        sess, sender_telegram_id=telegram_id, contact_user_id=telegram_id, raw_phone=phone,
    )).ok
    return u


def _auth(user: User, tenant: Tenant | None = None) -> dict:
    from app.core.security import issue_token
    h = {"Authorization": f"Bearer {issue_token(user_id=str(user.id))}"}
    if tenant is not None:
        h["X-Tenant-Slug"] = tenant.slug
    return h


# ── a typed number proves nothing ───────────────────────────────────────────

class TestTypedPhoneIsNotIdentity:
    async def test_an_attacker_cannot_capture_a_customers_notifications(self, api):
        """The concrete attack. Type the victim's number into your own profile
        and, under the old lookup, their shop's messages came to you."""
        _client, sess = api
        t = await _tenant(sess)
        victim = Customer(tenant_id=t.id, full_name="Victim", phone=UZ_A)
        sess.add(victim)
        # The attacker proves a DIFFERENT number, then types the victim's into
        # the contact field they control.
        attacker = await _prove(sess, 60001, UZ_B)
        attacker.phone = UZ_A
        await sess.commit()

        assert await customer_identity.reachable_telegram_id(sess, victim) is None
        assert await find_verified_owner(sess, UZ_A) is None

    async def test_a_typed_number_never_makes_somebody_known(self, api):
        _client, sess = api
        t = await _tenant(sess)
        c = Customer(tenant_id=t.id, full_name="Somebody", phone=UZ_A)
        sess.add(c)
        typed_only = User(telegram_id=60002, first_name="Typed", phone=UZ_A)
        sess.add(typed_only)
        await sess.commit()

        known = await customer_identity.known_telegram_ids(sess, [UZ_A])
        assert known == {}
        assert customer_identity.status_for(c, known) == customer_identity.NEW

    async def test_a_proved_number_does_make_somebody_known(self, api):
        _client, sess = api
        t = await _tenant(sess)
        c = Customer(tenant_id=t.id, full_name="Somebody", phone=UZ_A)
        sess.add(c)
        await _prove(sess, 60003, UZ_A)
        await sess.commit()

        known = await customer_identity.known_telegram_ids(sess, [UZ_A])
        assert known and customer_identity.status_for(c, known) == customer_identity.KNOWN
        assert await customer_identity.reachable_telegram_id(sess, c) == 60003

    async def test_a_linked_customer_is_joined_and_is_its_own_recipient(self, api):
        _client, sess = api
        t = await _tenant(sess)
        c = Customer(tenant_id=t.id, full_name="Linked", phone=UZ_A, telegram_id=60004)
        sess.add(c)
        await sess.commit()
        assert customer_identity.status_for(c, {}) == customer_identity.JOINED
        assert await customer_identity.reachable_telegram_id(sess, c) == 60004

    async def test_a_foreign_proof_is_not_a_recipient(self, api):
        _client, sess = api
        t = await _tenant(sess)
        c = Customer(tenant_id=t.id, full_name="Abroad", phone=FOREIGN)
        sess.add(c)
        await _prove(sess, 60005, FOREIGN)
        await sess.commit()
        assert await customer_identity.reachable_telegram_id(sess, c) is None
        assert await find_verified_owner(sess, FOREIGN) is None

    async def test_the_batch_and_single_lookups_agree(self, api):
        _client, sess = api
        await _prove(sess, 60006, UZ_A)
        User(telegram_id=60007, first_name="Typed", phone=UZ_B)
        await sess.commit()
        one = await find_verified_owner(sess, "+998 90 111 22 33")
        batch = await find_verified_owners(sess, ["998901112233", UZ_B])
        assert one is not None and one.telegram_id == 60006
        assert list(batch.values()) == [60006]

    def test_no_module_matches_identity_on_the_typed_column(self):
        """One implementation, and it reads the proved column."""
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        offenders = []
        for path in root.rglob("*.py"):
            src = path.read_text()
            if re.search(r"phone_identity\(\s*User\.phone\s*\)", src):
                offenders.append(path.relative_to(root).as_posix())
        assert offenders == [], offenders


# ── an already-linked customer is not invited ───────────────────────────────

class TestNoInviteForALinkedCustomer:
    async def test_creating_a_customer_for_a_proved_person_links_and_does_not_invite(self, api):
        """The exact journey: proof first, then the shop enters them."""
        client, sess = api
        t = await _tenant(sess)
        owner = await _owner(sess, t, 61001)
        await _prove(sess, 61002, UZ_A)
        await sess.commit()

        r = await client.post("/api/tenant/customers", headers=_auth(owner, t),
                              json={"full_name": "Example User", "phone": UZ_A})
        assert r.status_code in (200, 201), r.text
        body = r.json()

        assert body["telegram_status"] == "joined"
        assert body["invite"] is None, "a connected customer must not be handed a link"

        created = (await sess.execute(
            select(Customer).where(Customer.tenant_id == t.id, Customer.phone == UZ_A)
        )).scalar_one()
        assert created.telegram_id == 61002

        invites = (await sess.execute(
            select(Invitation).where(Invitation.tenant_id == t.id)
        )).scalars().all()
        assert invites == [], "no Invitation row may be created"
        assert body.get("notified") is False, "nothing to invite them to"

        # The customer's own linked id is the shop's business — it owns that
        # relationship. What must not appear is anything GLOBAL about the
        # matched account: its user id, its proved number, its provenance.
        blob = r.text
        for leak in ("verified_phone", "phone_eligibility", "verified_phone_source",
                     "platform_role", "display_name"):
            assert leak not in blob, leak

    async def test_an_unknown_number_still_gets_its_invitation(self, api):
        client, sess = api
        t = await _tenant(sess)
        owner = await _owner(sess, t, 61003)
        await sess.commit()
        r = await client.post("/api/tenant/customers", headers=_auth(owner, t),
                              json={"full_name": "Nobody Yet", "phone": UZ_B})
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["telegram_status"] == "new"
        assert body["invite"] and body["invite"]["token"]

    async def test_resending_refuses_for_a_linked_customer(self, api):
        client, sess = api
        t = await _tenant(sess)
        owner = await _owner(sess, t, 61004)
        c = Customer(tenant_id=t.id, full_name="Linked", phone=UZ_A, telegram_id=61005)
        sess.add(c)
        await sess.commit()

        r = await client.post(f"/api/tenant/customers/{c.id}/invite", headers=_auth(owner, t))
        assert r.status_code in (200, 201), r.text
        assert r.json()["invite"] is None
        assert r.json()["already_linked"] is True
        rows = (await sess.execute(
            select(Invitation).where(Invitation.tenant_id == t.id)
        )).scalars().all()
        assert rows == []

    async def test_resending_still_works_for_an_unlinked_customer(self, api):
        client, sess = api
        t = await _tenant(sess)
        owner = await _owner(sess, t, 61006)
        c = Customer(tenant_id=t.id, full_name="Unlinked", phone=UZ_B)
        sess.add(c)
        await sess.commit()
        r = await client.post(f"/api/tenant/customers/{c.id}/invite", headers=_auth(owner, t))
        assert r.status_code in (200, 201)
        assert r.json().get("token")

    async def test_one_status_definition_across_create_list_and_detail(self, api):
        client, sess = api
        t = await _tenant(sess)
        owner = await _owner(sess, t, 61007)
        await _prove(sess, 61008, UZ_A)
        await sess.commit()

        created = (await client.post("/api/tenant/customers", headers=_auth(owner, t),
                                     json={"full_name": "Linked One", "phone": UZ_A})).json()
        listing = (await client.get("/api/tenant/customers", headers=_auth(owner, t))).json()
        rows = listing["items"] if isinstance(listing, dict) else listing
        row = next(r for r in rows if r["id"] == created["id"])
        assert created["telegram_status"] == row["telegram_status"] == "joined"


# ── the concurrency proof ───────────────────────────────────────────────────

async def test_a_real_constraint_race_isolates_to_one_tenant(api, migrated_db):
    """Against real Postgres constraints, not a stand-in.

    A links cleanly, B loses a genuine unique-index race, C is ambiguous. The
    outer transaction must still commit with the proof and A's link intact, B's
    mutation gone, and the session usable throughout.
    """
    from app.services import identity_link as link_mod

    _client, sess = api
    migrated_engine = migrated_db
    a, b, c = await _tenant(sess, "a"), await _tenant(sess, "b"), await _tenant(sess, "c")
    ca = Customer(tenant_id=a.id, full_name="A", phone=UZ_A)
    cb = Customer(tenant_id=b.id, full_name="B", phone=UZ_A)
    cc = Customer(tenant_id=c.id, full_name="C", phone=UZ_A)
    for row in (ca, cb, cc):
        sess.add(row)
    user = await _prove(sess, 62001, UZ_A)
    await sess.commit()
    ids = {"a": ca.id, "b": cb.id, "c": cc.id}
    b_id, c_id = b.id, c.id

    real_candidates = link_mod.find_candidates
    other_maker = async_sessionmaker(migrated_engine, expire_on_commit=False)

    async def _race_in_b_ambiguous_in_c(db, *, tenant_id, e164):
        rows = await real_candidates(db, tenant_id=tenant_id, e164=e164)
        if tenant_id == b_id:
            # A CONCURRENT writer takes the binding between this read and our
            # write — committed from its own session, so the partial unique
            # index is what refuses us, not an in-memory check.
            async with other_maker() as other:
                other.add(Customer(tenant_id=b_id, full_name="B-other",
                                   phone=UZ_B, telegram_id=62001))
                await other.commit()
        if tenant_id == c_id:
            return rows * 2                     # two candidates, as legacy data can be
        return rows

    link_mod.find_candidates = _race_in_b_ambiguous_in_c
    try:
        sweep = await link_mod.reconcile_all_tenants(sess, user=user)
        await sess.commit()                     # must not raise PendingRollbackError
    finally:
        link_mod.find_candidates = real_candidates

    assert sweep.linked == 1
    assert sweep.needs_operator == 2            # B refused, C ambiguous

    fresh = {k: (await sess.execute(
        select(Customer).where(Customer.id == v))).scalar_one() for k, v in ids.items()}
    who = (await sess.execute(select(User).where(User.telegram_id == 62001))).scalar_one()

    assert fresh["a"].telegram_id == 62001      # the clean tenant survived
    assert fresh["b"].telegram_id is None       # the racing write did not
    assert fresh["c"].telegram_id is None       # the ambiguous one untouched
    assert who.verified_phone == UZ_A           # the proof survived

    # A retry re-attempts nothing it already did: A comes back `already`, and
    # the two that were left alone are simply re-evaluated. (C links this time —
    # its ambiguity was the stand-in above, and it is gone.)
    again = await link_mod.reconcile_all_tenants(sess, user=user)
    await sess.commit()
    assert again.already >= 1, "A must be idempotent, not linked a second time"
    a_after = (await sess.execute(
        select(Customer).where(Customer.id == ids["a"]))).scalar_one()
    assert a_after.telegram_id == 62001
    still = (await sess.execute(select(User).where(User.telegram_id == 62001))).scalar_one()
    assert still.verified_phone == UZ_A


async def test_an_invitation_is_not_minted_twice_for_one_customer(api):
    client, sess = api
    t = await _tenant(sess)
    owner = await _owner(sess, t, 63001)
    await sess.commit()
    first = (await client.post("/api/tenant/customers", headers=_auth(owner, t),
                               json={"full_name": "Once", "phone": UZ_B})).json()
    assert first["invite"]
    rows = (await sess.execute(
        select(Invitation).where(Invitation.tenant_id == t.id))).scalars().all()
    assert len(rows) == 1


def test_the_write_happens_inside_its_savepoint():
    """`begin_nested()` FLUSHES before it establishes the savepoint.

    A mutation assigned above the `async with` is therefore pushed out ahead of
    the thing meant to contain it. The behavioural race above passes either way
    on this SQLAlchemy version — `expire()` re-reads the row afterwards and
    hides the difference — so the ordering itself is pinned here, where a
    regression is visible.
    """
    import inspect
    import re

    from app.services import identity_link, telegram_contact

    for mod in (identity_link.reconcile, telegram_contact.record_verified_contact):
        src = inspect.getsource(mod)
        block = re.search(
            r"async with db\.begin_nested\(\):\n((?:[ \t]+.*\n)+)", src,
        )
        assert block, f"{mod.__name__} no longer opens a savepoint"
        body = block.group(1)
        assert "await db.flush()" in body, mod.__name__
        # Every assignment to a persisted attribute belongs inside the block.
        assigns = re.findall(r"^\s*(?:target|user)\.\w+\s*=", src, re.M)
        inside = re.findall(r"^\s*(?:target|user)\.\w+\s*=", body, re.M)
        assert assigns and len(inside) == len(assigns), (
            f"{mod.__name__} mutates outside its savepoint"
        )


@pytest.mark.parametrize("banned", ["FB-01", "Batch 3", "Phase 9"])
def test_no_audit_narrative_in_touched_source(banned):
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("app/services/customer_identity.py", "app/services/identity_link.py",
                 "app/api/tenant/customers.py", "app/core/invite_codes.py"):
        assert banned not in (root / name).read_text(), name


def test_customer_status_is_not_recomputed_after_reconciliation():
    """One definition. A second typed-phone calculation after the authoritative
    LinkResult is how the two answers drift apart."""
    import inspect

    from app.api.tenant import customers as mod
    src = inspect.getsource(mod.create_customer)
    assert "link_new_customer" in src
    assert "link.ok" in src
    # The retired lookup name must not reappear anywhere.
    assert "find_platform_user" not in src
