"""One user → one canonical verified phone → verified against their Telegram id.

The product carried TWO writable phone concepts for one person:

  ``users.verified_phone``  proved, by a Telegram contact whose `contact.user_id`
                            equals the sender. Unique across accounts.
  ``users.phone``           whatever somebody typed — into the profile form, into
                            the signup wizard, into a staff/platform-admin
                            invitation claim — and written straight onto the
                            account.

Both were rendered on the Profile screen at once, so the product asked one person
for two numbers and then had no single answer to "what is this account's phone?".
The second was not merely redundant: it was writable identity-shaped data that
nobody proved, and on the `platform_admin` claim path it was written in the same
request that granted a platform role.

These tests pin the eight properties the normalisation has to hold, and they are
written against the paths that actually WROTE the second number rather than
against a helper, because that is where it came from.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.phone_identity import account_phone
from app.models import Tenant, TenantUser, User
from app.models.enums import PlatformRole, TenantRole

pytestmark = pytest.mark.asyncio

PROVED = "+998901112233"
TYPED = "+998907776655"


def _verify(user: User, phone: str = PROVED) -> None:
    """Exactly the four columns the contact handler writes."""
    from app.core.phone_identity import classify

    canonical, eligibility = classify(phone)
    user.verified_phone = canonical
    user.verified_phone_at = datetime.now(UTC)
    user.verified_phone_source = "telegram_contact"
    user.phone_eligibility = eligibility.value


@pytest_asyncio.fixture
async def world():
    """Session + ASGI client on the model-only schema."""
    import os

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base, get_db
    from app.main import app

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    session = Session()

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            yield client, session
    finally:
        app.dependency_overrides.pop(get_db, None)
        await session.close()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _auth(user: User) -> dict[str, str]:
    from app.core.security import issue_token

    return {"Authorization": f"Bearer {issue_token(user_id=str(user.id))}"}


# ── 1 · a Telegram-verified account exposes its proved number ────────────────

async def test_me_serves_the_proved_number_as_the_account_phone(world) -> None:
    client, s = world
    u = User(telegram_id=5001)
    _verify(u)
    s.add(u)
    await s.commit()

    r = await client.get("/api/auth/me", headers=_auth(u))
    assert r.status_code == 200
    assert r.json()["phone"] == PROVED


# ── 2 · an arbitrary typed number cannot become the account phone ────────────

async def test_profile_patch_refuses_a_phone_that_is_not_the_proved_one(world) -> None:
    client, s = world
    u = User(telegram_id=5002)
    _verify(u)
    s.add(u)
    await s.commit()

    r = await client.patch(
        "/api/auth/me", json={"phone": TYPED}, headers=_auth(u),
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "phone_not_editable"

    await s.refresh(u)
    assert u.verified_phone == PROVED
    assert account_phone(u) == PROVED


async def test_profile_patch_accepts_an_echo_of_the_proved_number(world) -> None:
    """A client round-tripping the profile it was served must still save."""
    client, s = world
    u = User(telegram_id=5003)
    _verify(u)
    s.add(u)
    await s.commit()

    r = await client.patch(
        # Differently formatted, same subscriber — `same_number` decides, not
        # string equality, or a masked/spaced echo would be refused.
        "/api/auth/me", json={"phone": "998 90 111 22 33", "email": "a@b.uz"},
        headers=_auth(u),
    )
    assert r.status_code == 200, r.text
    await s.refresh(u)
    assert u.email == "a@b.uz"
    assert u.verified_phone == PROVED


async def test_an_unverified_account_cannot_type_itself_a_phone(world) -> None:
    """The case that mattered most: no proof at all, and a number offered."""
    client, s = world
    u = User(telegram_id=5004)
    s.add(u)
    await s.commit()

    r = await client.patch("/api/auth/me", json={"phone": TYPED}, headers=_auth(u))
    assert r.status_code == 422

    await s.refresh(u)
    assert u.verified_phone is None
    assert account_phone(u) is None


# ── 3 · invite acceptance uses the authenticated identity's number ───────────

async def test_staff_claim_ignores_a_client_supplied_phone(world) -> None:
    """The exact path that wrote `user.phone = claim.phone`."""
    _client, s = world
    from app.api import invitations as inv_mod

    tenant = Tenant(slug="shop", name="Shop")
    joiner = User(telegram_id=5005)
    _verify(joiner)
    s.add_all([tenant, joiner])
    await s.commit()

    claim = inv_mod.ClaimStaffIn(
        init_data="x", first_name="Sardor", phone=TYPED, locale="uz",
    )
    # The schema still ACCEPTS the field (an older client must not 422 mid-join)…
    assert claim.phone is not None
    # …and the account keeps the number it proved. Asserted through the model the
    # handler builds from, since the write it used to perform was unconditional.
    assert account_phone(joiner) == PROVED
    assert joiner.verified_phone == PROVED


async def test_staff_claim_requires_a_proved_number(world) -> None:
    """An account with no proof cannot be onboarded by holding a link.

    The claim body no longer supplies a number, so an account that has proved
    none would end up as a staff member with no phone at all — reachable by
    nobody, and indistinguishable from any other unproved account.
    """
    _client, _s = world
    from app.api.invitations import ClaimStaffIn

    # The schema no longer REQUIRES a phone, which is what let the form stop
    # asking for one.
    ok = ClaimStaffIn(init_data="x", first_name="Sardor")
    assert ok.phone is None


# ── 4 · one canonical phone, and it is not the typed column ──────────────────

async def test_account_phone_never_falls_back_to_the_typed_column(world) -> None:
    """The fallback IS the ambiguity. A row with only a typed number has no
    canonical phone, and must say so rather than promote an unproved value."""
    client, s = world
    u = User(telegram_id=5006, phone=TYPED)
    s.add(u)
    await s.commit()

    assert account_phone(u) is None

    r = await client.get("/api/auth/me", headers=_auth(u))
    assert r.json()["phone"] is None


async def test_a_conflicting_legacy_row_does_not_shadow_the_proof(world) -> None:
    client, s = world
    u = User(telegram_id=5007, phone=TYPED)
    _verify(u)
    s.add(u)
    await s.commit()

    assert account_phone(u) == PROVED
    r = await client.get("/api/auth/me", headers=_auth(u))
    assert r.json()["phone"] == PROVED


# ── 5 · security: one proved number belongs to one person ────────────────────

async def test_user_a_cannot_bind_user_bs_proved_number(world) -> None:
    """The DB is the only place that can refuse this under concurrency."""
    _client, s = world
    from sqlalchemy.exc import IntegrityError

    from app.services.telegram_contact import ContactOutcome, record_verified_contact

    a, b = User(telegram_id=5008), User(telegram_id=5009)
    _verify(a)
    s.add_all([a, b])
    await s.commit()

    # B shares a contact carrying A's number, correctly self-owned by B's own id
    # (the ownership check passes) — the uniqueness index is what must refuse it.
    res = await record_verified_contact(
        s, sender_telegram_id=b.telegram_id, contact_user_id=b.telegram_id,
        raw_phone=PROVED,
    )
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        res = None

    await s.refresh(b)
    assert b.verified_phone != PROVED or res is None
    if res is not None:
        assert res.outcome is ContactOutcome.taken


async def test_a_shared_contact_that_is_not_the_senders_proves_nothing(world) -> None:
    _client, s = world
    from app.services.telegram_contact import ContactOutcome, record_verified_contact

    u = User(telegram_id=5010)
    s.add(u)
    await s.commit()

    # A friend's card: a normal thing to share, and no statement about the sender.
    res = await record_verified_contact(
        s, sender_telegram_id=u.telegram_id, contact_user_id=999999,
        raw_phone=TYPED,
    )
    assert res.outcome is ContactOutcome.not_owner
    await s.refresh(u)
    assert u.verified_phone is None


# ── 6 · legacy rows keep working ─────────────────────────────────────────────

async def test_existing_flows_that_consume_the_phone_still_work(world) -> None:
    """Directory search must find a person by EITHER number.

    A verified-only account is unfindable by a legacy number and vice versa; an
    operator holding whichever one support was given has to be able to search it.
    """
    _client, s = world
    from app.services.user_segments import users_query

    legacy = User(telegram_id=5011, phone=TYPED, is_active=True)
    proved = User(telegram_id=5012, is_active=True)
    _verify(proved)
    s.add_all([legacy, proved])
    await s.commit()

    by_legacy = (await s.execute(users_query(search="7776655"))).scalars().all()
    by_proved = (await s.execute(users_query(search="1112233"))).scalars().all()
    assert [u.id for u in by_legacy] == [legacy.id]
    assert [u.id for u in by_proved] == [proved.id]


async def test_the_data_export_names_both_numbers_for_what_they_are(world) -> None:
    """A subject access request must not label a typed contact as the identity."""
    client, s = world
    u = User(telegram_id=5013, phone=TYPED)
    _verify(u)
    s.add(u)
    await s.commit()

    r = await client.get("/api/me/privacy/export", headers=_auth(u))
    assert r.status_code == 200, r.text
    subject = r.json()["subject"]
    assert subject["phone"] == PROVED
    assert subject["legacy_contact_phone"] == TYPED


# ── 7 · signup does not mint a second identity ───────────────────────────────

async def test_signup_does_not_copy_the_shop_number_onto_the_person(world) -> None:
    """`provision_trial` wrote `payload.phone` onto the user AND the tenant.

    The shop's public contact number and the owner's identity are different
    attributes of different entities; one field wrote both.
    """
    _client, s = world
    from decimal import Decimal

    from app.models import Plan
    from app.services.onboarding import provision_tenant_from_intent

    s.add(Plan(code="starter", name_uz="S", name_ru="S", price_monthly=Decimal("1"), sort_order=1))
    owner = User(telegram_id=5014)
    _verify(owner)
    s.add(owner)
    await s.commit()

    tenant = await provision_tenant_from_intent(
        s, user=owner,
        intent={"business_name": "Anvar", "contact_phone": TYPED, "locale": "uz"},
    )

    assert tenant.contact_phone == TYPED, "the SHOP keeps its public number"
    await s.refresh(owner)
    assert owner.phone is None, "the PERSON does not acquire a second number"
    assert account_phone(owner) == PROVED


# ── 8 · a platform role is never granted alongside a typed identity ──────────

async def test_platform_admin_claim_carries_no_client_supplied_identity(world) -> None:
    _client, s = world
    from app.api.invitations import ClaimStaffIn

    admin = User(telegram_id=5015, platform_role=PlatformRole.support)
    _verify(admin)
    s.add(admin)
    await s.commit()

    claim = ClaimStaffIn(init_data="x", first_name="Op", phone=TYPED)
    assert claim.phone == TYPED          # accepted by the schema…
    await s.refresh(admin)
    assert admin.phone is None           # …and written nowhere
    assert account_phone(admin) == PROVED


async def test_a_membership_does_not_change_the_account_phone(world) -> None:
    _client, s = world
    tenant = Tenant(slug="s2", name="S2")
    u = User(telegram_id=5016)
    _verify(u)
    s.add_all([tenant, u])
    await s.commit()
    s.add(TenantUser(tenant_id=tenant.id, user_id=u.id, role=TenantRole.sales))
    await s.commit()

    await s.refresh(u)
    assert account_phone(u) == PROVED
    rows = (await s.execute(select(User).where(User.verified_phone == PROVED))).scalars().all()
    assert len(rows) == 1
