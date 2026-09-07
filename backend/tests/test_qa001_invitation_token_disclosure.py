"""QA-001 — invitation-token disclosure lets a lower tenant role become `owner`.

Two independent defects compose into a privilege escalation, and both are fixed
here because either one alone still leaves a usable path:

  1. ``GET /api/tenant/invitations`` had **no role guard**. Every row it returns
     carries the raw claim ``token`` plus the ready-made ``web_url`` /
     ``telegram_url``, so anyone who can read the list can claim anything in it.
     A ``sales`` clerk — the lowest tenant role — could read an ``role="owner"``
     staff invitation minted by the owner. The mint path already implements the
     ceiling this read defeats (only an owner may mint an owner-role invite);
     the read must draw the same line, and must additionally not hand a manager
     the one invitation the ceiling forbids them to create.

  2. The staff-claim branch rewrote an **existing** membership
     (``existing.role = role``, and reactivation of a revoked member). Promotion,
     demotion and re-activation are staff-management operations (Phase-2
     ROLE-011, owner-only) — never a side effect of holding a bearer link.

Everything here is DB-backed and driven over HTTP through the real
``resolve_tenant`` dependency, real tokens and real HMAC-signed Telegram
initData: the escalation was reproduced end to end before the fix, so the tests
that prove it closed must run against the same surface, not against source text.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import pytest
import pytest_asyncio

from app.core import invite_codes

pytestmark = pytest.mark.asyncio

INVITATIONS = "/api/tenant/invitations"

# Owner-only reads used to demonstrate the escalation's payoff (they answered
# 403 for the clerk before the claim and 200 after it).
OWNER_ONLY_READS = ("/api/tenant/billing/topups", "/api/tenant/reports/aging")

TG_OWNER, TG_MANAGER, TG_SALES, TG_OUTSIDER, TG_NEWCOMER = (
    910001, 910002, 910003, 910004, 910005,
)


async def _prove_phone(ctx, telegram_id: int, phone: str) -> None:
    """Do what a real joiner does before claiming: prove their number.

    Since the phone-identity normalisation a staff or platform-admin claim no
    longer accepts a number in its body — the account's phone is the one proved
    against its Telegram id, or none — so an account with no proof gets
    `{"ok": false, identity_state: "VERIFICATION_REQUIRED"}` rather than a
    membership. That is the product rule, not an obstacle to route around, so
    this runs the REAL proof path (`record_verified_contact`) exactly as the bot
    does when the person taps "Share my number", including its ownership check.

    The user row is created first because that is the real order too: /start
    creates the account, the contact share then proves its number.
    """
    from app.models import User
    from app.services.telegram_contact import record_verified_contact

    async with ctx["Session"]() as s:
        from sqlalchemy import select

        u = (await s.execute(
            select(User).where(User.telegram_id == telegram_id)
        )).scalar_one_or_none()
        if u is None:
            u = User(telegram_id=telegram_id)
            s.add(u)
            await s.flush()
        res = await record_verified_contact(
            s, sender_telegram_id=telegram_id,
            contact_user_id=telegram_id,      # self-owned: the only thing that proves it
            raw_phone=phone,
        )
        assert res.ok, f"the test's own proof step failed: {res.outcome}"
        await s.commit()


def _init_data(telegram_id: int, *, first_name: str = "Joiner") -> str:
    """A correctly signed Telegram Mini-App initData string for `telegram_id`.

    Signed with the same bot token the app is configured with, so the claim runs
    through the real HMAC-verified join path rather than around it.
    """
    from app.config import settings

    bot_token = settings.telegram_bot_token
    fields = {
        "user": json.dumps(
            {"id": telegram_id, "first_name": first_name, "username": f"u{telegram_id}"}
        ),
        "auth_date": str(int(time.time())),
        "query_id": "AAH",
    }
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return urlencode(
        {**fields, "hash": hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()}
    )


@pytest_asyncio.fixture
async def inv_ctx():
    """Two tenants. Tenant A has an owner, a manager and a sales clerk (each with
    a Telegram id, so any of them can attempt a claim as themselves); tenant B has
    its own owner, for the isolation assertion."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Plan, Tenant, TenantUser, User
    from app.models.enums import TenantRole

    engine = create_async_engine(url)
    # (QA-R-002) DB tier is mandatory once TEST_DATABASE_URL is set — setup errors
    # (refused connection, privilege, bad schema) must FAIL, never self-skip green.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            # A roomy plan so the staff cap never masks an authorization result.
            s.add(Plan(code="qa001", name_uz="QA", name_ru="QA",
                       price_monthly=0, max_staff=50))
            ta = Tenant(slug="qa001-a", name="QA001 A", plan_code="qa001")
            tb = Tenant(slug="qa001-b", name="QA001 B", plan_code="qa001")
            s.add_all([ta, tb])
            await s.flush()
            owner = User(telegram_id=TG_OWNER, first_name="Owner")
            manager = User(telegram_id=TG_MANAGER, first_name="Manager")
            sales = User(telegram_id=TG_SALES, first_name="Sales")
            outsider = User(telegram_id=TG_OUTSIDER, first_name="Outsider")
            s.add_all([owner, manager, sales, outsider])
            await s.flush()
            s.add_all([
                TenantUser(tenant_id=ta.id, user_id=owner.id, role=TenantRole.owner),
                TenantUser(tenant_id=ta.id, user_id=manager.id, role=TenantRole.manager),
                TenantUser(tenant_id=ta.id, user_id=sales.id, role=TenantRole.sales),
                TenantUser(tenant_id=tb.id, user_id=outsider.id, role=TenantRole.owner),
            ])
            await s.commit()
            ctx = {
                "tenant_a": ta.id,
                "sales_user_id": sales.id,
                "manager_user_id": manager.id,
                "owner": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
                "manager": {"Authorization": f"Bearer {issue_token(user_id=str(manager.id))}"},
                "sales": {"Authorization": f"Bearer {issue_token(user_id=str(sales.id))}"},
                "outsider": {"Authorization": f"Bearer {issue_token(user_id=str(outsider.id))}"},
                "Session": Session,
            }
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            yield client, ctx
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _mint(client, headers, **body) -> dict:
    r = await client.post(INVITATIONS, headers=headers, json=body)
    assert r.status_code == 201, f"mint {body}: {r.status_code} {r.text[:300]}"
    return r.json()


async def _role_of(Session, tenant_id, user_id):
    from sqlalchemy import select

    from app.models import TenantUser

    async with Session() as s:
        row = (await s.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id, TenantUser.user_id == user_id,
            )
        )).scalar_one()
        return row.role.value, row.is_active


# ── the read guard ─────────────────────────────────────────────────────────

async def test_anonymous_cannot_list_invitations(inv_ctx) -> None:
    client, _ = inv_ctx
    r = await client.get(INVITATIONS)
    assert r.status_code == 401


async def test_sales_cannot_list_invitations(inv_ctx) -> None:
    """The disclosure itself: a clerk must not read the list at all — every row
    in it is a bearer credential."""
    client, ctx = inv_ctx
    await _mint(client, ctx["owner"], kind="staff", role="owner")
    r = await client.get(INVITATIONS, headers=ctx["sales"])
    assert r.status_code == 403, f"sales must not read invitations, got {r.status_code}"
    assert r.json()["detail"] == "owner_or_manager_only"


@pytest.mark.parametrize(
    "query",
    ["", "?kind=staff", "?kind=customer", "?only_open=false", "?only_open=true",
     "?kind=staff&only_open=false"],
    ids=["plain", "staff", "customer", "closed", "open", "staff-closed"],
)
async def test_sales_is_denied_on_every_filter_variant(inv_ctx, query: str) -> None:
    """A guard that only covers the default query is not a guard."""
    client, ctx = inv_ctx
    r = await client.get(f"{INVITATIONS}{query}", headers=ctx["sales"])
    assert r.status_code == 403, f"sales reached {query!r}: {r.status_code}"


async def test_owner_lists_the_invitations_they_manage(inv_ctx) -> None:
    client, ctx = inv_ctx
    owner_inv = await _mint(client, ctx["owner"], kind="staff", role="owner")
    sales_inv = await _mint(client, ctx["owner"], kind="staff", role="sales")
    r = await client.get(INVITATIONS, headers=ctx["owner"])
    assert r.status_code == 200
    rows = r.json()
    # Identified by id, not by credential: storage keeps a verifier, so a list
    # response has no credential to carry — which is the stronger property.
    ids = {row["id"] for row in rows}
    assert owner_inv["id"] in ids, "an owner must see the owner-role invite they minted"
    assert sales_inv["id"] in ids
    assert all(row["token"] is None for row in rows), (
        "a listing must not be able to hand back a credential"
    )


@pytest.mark.parametrize(
    "query",
    ["", "?kind=staff", "?only_open=false", "?only_open=true", "?kind=staff&only_open=false"],
    ids=["plain", "staff", "closed", "open", "staff-closed"],
)
async def test_manager_never_receives_an_owner_invitations_credentials(
    inv_ctx, query: str,
) -> None:
    """A manager keeps the staff-invitation workflow, but the owner-role invite is
    exactly the thing the mint ceiling forbids them to create — so its token, web
    URL and Telegram URL must never reach them, under any filter."""
    client, ctx = inv_ctx
    owner_inv = await _mint(client, ctx["owner"], kind="staff", role="owner")
    r = await client.get(f"{INVITATIONS}{query}", headers=ctx["manager"])
    assert r.status_code == 200, f"manager lost the workflow at {query!r}: {r.status_code}"
    body = r.json()
    assert owner_inv["id"] not in {row["id"] for row in body}, (
        f"owner-role invitation leaked to a manager at {query!r}"
    )
    blob = json.dumps(body)
    for secret in (owner_inv["token"], owner_inv["web_url"], owner_inv["telegram_url"]):
        assert secret not in blob, f"{secret!r} leaked to a manager at {query!r}"


async def test_manager_retains_the_non_owner_invitation_workflow(inv_ctx) -> None:
    """Phase-2 ROLE-009/ROLE-015 put staff onboarding in the manager's hands; the
    guard must narrow the owner-role row only."""
    client, ctx = inv_ctx
    minted = await _mint(client, ctx["manager"], kind="staff", role="sales")
    cust = await _mint(client, ctx["manager"], kind="customer")
    r = await client.get(INVITATIONS, headers=ctx["manager"])
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()}
    assert minted["id"] in ids, "manager lost their own staff invitation"
    assert cust["id"] in ids, "manager lost the customer invitation workflow"


async def test_owner_role_mint_ceiling_still_blocks_a_manager(inv_ctx) -> None:
    """The pre-existing ceiling the read path defeated must remain."""
    client, ctx = inv_ctx
    r = await client.post(INVITATIONS, headers=ctx["manager"],
                          json={"kind": "staff", "role": "owner"})
    assert r.status_code == 403
    assert r.json()["detail"] == "forbidden_role_escalation"


async def test_sales_cannot_mint_or_revoke(inv_ctx) -> None:
    """Route-contract guard: all three tenant invitation routes deny `sales`, so a
    future refactor cannot quietly reopen one of them."""
    client, ctx = inv_ctx
    minted = await _mint(client, ctx["owner"], kind="staff", role="sales")
    assert (await client.post(
        INVITATIONS, headers=ctx["sales"], json={"kind": "customer"},
    )).status_code == 403
    assert (await client.delete(
        f"{INVITATIONS}/{minted['id']}", headers=ctx["sales"],
    )).status_code == 403


async def test_tenant_isolation_of_the_invitation_list(inv_ctx) -> None:
    client, ctx = inv_ctx
    mine = await _mint(client, ctx["owner"], kind="staff", role="sales")
    r = await client.get(INVITATIONS, headers=ctx["outsider"])
    assert r.status_code == 200
    assert mine["id"] not in {row["id"] for row in r.json()}


# ── the claim path ─────────────────────────────────────────────────────────

async def test_end_to_end_escalation_no_longer_works(inv_ctx) -> None:
    """The audit's five-step chain, replayed. Both halves must hold: the clerk
    cannot read the token, and even handed the token they cannot promote
    themselves with it."""
    client, ctx = inv_ctx
    before = await _role_of(ctx["Session"], ctx["tenant_a"], ctx["sales_user_id"])
    assert before == ("sales", True)

    # STEP 1 — the owner mints a legitimate owner-role invite.
    inv = await _mint(client, ctx["owner"], kind="staff", role="owner")

    # STEP 2 — the clerk tries to read it.
    listed = await client.get(INVITATIONS, headers=ctx["sales"])
    assert listed.status_code == 403

    # STEP 3 — even with the token leaked by other means, the claim is refused.
    claimed = await client.post(
        f"/api/inv/{inv['token']}/claim",
        json={"init_data": _init_data(TG_SALES, first_name="Sales"),
              "first_name": "Sales", "phone": "+998901112233"},
    )
    assert claimed.status_code == 409, (
        f"an existing member claimed a staff invite: {claimed.status_code} {claimed.text[:300]}"
    )
    assert claimed.json()["detail"] == "already_a_member"

    # STEP 4 — the membership is untouched.
    assert await _role_of(ctx["Session"], ctx["tenant_a"], ctx["sales_user_id"]) == ("sales", True)

    # STEP 5 — the owner-only reads stay 403.
    for path in OWNER_ONLY_READS:
        r = await client.get(path, headers=ctx["sales"])
        assert r.status_code == 403, f"{path} opened to sales: {r.status_code}"


async def test_rejected_claim_does_not_consume_the_invitation(inv_ctx) -> None:
    """The legitimate invitee must still be able to use the link — a refused claim
    that burned the token would turn the defect into a denial of service."""
    from sqlalchemy import select

    from app.models import Invitation

    client, ctx = inv_ctx
    inv = await _mint(client, ctx["owner"], kind="staff", role="owner")
    r = await client.post(
        f"/api/inv/{inv['token']}/claim",
        json={"init_data": _init_data(TG_MANAGER, first_name="Manager"),
              "first_name": "Manager", "phone": "+998901112244"},
    )
    assert r.status_code == 409

    async with ctx["Session"]() as s:
        row = (await s.execute(
            select(Invitation).where(invite_codes.lookup_filter(inv["token"]))
        )).scalar_one()
        assert row.used_at is None, "a rejected claim consumed the token"
        assert row.used_by_user_id is None

    # …and the intended recipient (a genuinely new person) can still claim it,
    # once they have proved their number the way every joiner now does.
    await _prove_phone(ctx, TG_NEWCOMER, "+998901112255")
    ok = await client.post(
        f"/api/inv/{inv['token']}/claim",
        json={"init_data": _init_data(TG_NEWCOMER, first_name="NewOwner"),
              "first_name": "NewOwner"},
    )
    assert ok.status_code == 200, f"{ok.status_code} {ok.text[:300]}"
    assert ok.json()["role"] == "owner"


async def test_a_new_person_can_still_claim_a_staff_invitation(inv_ctx) -> None:
    from sqlalchemy import select

    from app.models import TenantUser, User

    client, ctx = inv_ctx
    inv = await _mint(client, ctx["owner"], kind="staff", role="sales")
    await _prove_phone(ctx, TG_NEWCOMER, "+998901112266")
    r = await client.post(
        f"/api/inv/{inv['token']}/claim",
        json={"init_data": _init_data(TG_NEWCOMER, first_name="Newbie"),
              "first_name": "Newbie"},
    )
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    assert r.json()["role"] == "sales"

    # The number on the account is the PROVED one. Nothing the claim body could
    # have said about a phone reached the account — the field is ignored.
    from app.core.phone_identity import same_number

    async with ctx["Session"]() as s:
        u = (await s.execute(
            select(User).where(User.telegram_id == TG_NEWCOMER)
        )).scalar_one()
        tu = (await s.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == ctx["tenant_a"], TenantUser.user_id == u.id,
            )
        )).scalar_one()
        assert tu.role.value == "sales" and tu.is_active
        assert same_number(u.verified_phone, "+998901112266")
        assert u.phone is None, "a claim body must not write the account's phone"


async def test_a_revoked_member_is_not_reactivated_by_a_claim(inv_ctx) -> None:
    """Re-activation is a staff-management decision too — a revoked clerk must not
    let themselves back in by replaying any staff link."""
    from sqlalchemy import select

    from app.models import TenantUser

    client, ctx = inv_ctx
    async with ctx["Session"]() as s:
        tu = (await s.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == ctx["tenant_a"],
                TenantUser.user_id == ctx["sales_user_id"],
            )
        )).scalar_one()
        tu.is_active = False
        await s.commit()

    inv = await _mint(client, ctx["owner"], kind="staff", role="sales")
    r = await client.post(
        f"/api/inv/{inv['token']}/claim",
        json={"init_data": _init_data(TG_SALES, first_name="Sales"),
              "first_name": "Sales", "phone": "+998901112277"},
    )
    assert r.status_code == 409
    assert await _role_of(ctx["Session"], ctx["tenant_a"], ctx["sales_user_id"]) == ("sales", False)


async def test_customer_invitation_claim_is_unaffected(inv_ctx) -> None:
    """The guard is scoped to the staff branch; customer self-onboarding still
    works end to end."""
    from sqlalchemy import select

    from app.models import Customer, User
    from app.services.telegram_contact import record_verified_contact

    client, ctx = inv_ctx
    inv = await _mint(client, ctx["owner"], kind="customer")

    # Self-onboarding now requires proved ownership: a customer relationship
    # carries contracts, so a typed number cannot create one. Establish the
    # proof exactly as the bot does, then claim.
    async with ctx["Session"]() as s:
        s.add(User(telegram_id=TG_NEWCOMER, first_name="Ali"))
        await s.flush()
        assert (await record_verified_contact(
            s, sender_telegram_id=TG_NEWCOMER, contact_user_id=TG_NEWCOMER,
            raw_phone="+998901112288",
        )).ok
        await s.commit()

    r = await client.post(
        f"/api/inv/{inv['token']}/claim",
        json={"init_data": _init_data(TG_NEWCOMER, first_name="Ali"),
              "full_name": "Ali Valiyev"},
    )
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    assert r.json()["ok"] is True, r.text[:300]
    async with ctx["Session"]() as s:
        c = (await s.execute(
            select(Customer).where(Customer.telegram_id == TG_NEWCOMER)
        )).scalar_one()
        assert c.tenant_id == ctx["tenant_a"]
