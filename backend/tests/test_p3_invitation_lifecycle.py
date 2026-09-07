"""#3 — the invitation lifecycle, for every kind: expiry, revocation, single use.

The owner-claim link was broken by a deep-link prefix mismatch (fixed on the
frontend). This file covers the other half of that finding: that the SERVER
enforces the lifecycle identically for every invitation kind, and that the
enforcement lives on the state-changing route rather than only on the read.

The distinction matters. `GET /inv/{token}` renders the form and `POST
/inv/{token}/claim` materialises a Customer or a TenantUser. If only the GET
checked expiry, anyone could skip it and post straight to the claim — so both
are asserted separately here rather than assuming the pair agrees.

Claims are exercised up to the point of Telegram verification. Expiry, reuse and
unknown-token rejection all happen BEFORE `verify_init_data`, which is the
ordering that makes them enforceable without a valid HMAC — and asserting that
ordering is itself the point: a token check placed after signature verification
would be unreachable for exactly the callers it is meant to stop.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

KINDS = ["customer", "staff", "guarantor"]


async def _mint(session_factory, *, tenant_id, kind: str, role: str | None = None,
                expires_in_hours: float = 24, used: bool = False) -> str:
    """Insert an invitation row directly — the create API is covered elsewhere,
    and going through it here would couple these lifecycle assertions to its
    permission rules."""
    from app.models import Invitation

    token = f"tk{uuid4().hex}"[:40]
    agen = session_factory()
    db = await agen.__anext__()
    try:
        db.add(Invitation(
            token=token,
            tenant_id=tenant_id,
            kind=kind,
            role=role,
            expires_at=datetime.now(UTC) + timedelta(hours=expires_in_hours),
            used_at=datetime.now(UTC) if used else None,
            preset={},
        ))
        await db.commit()
    finally:
        await agen.aclose()
    return token


@pytest.fixture
def factory(isolation_ctx):
    from app.db import get_db
    from app.main import app
    return app.dependency_overrides[get_db]


# ── a live invitation resolves ───────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_live_invitation_renders_its_form(isolation_ctx, factory, kind):
    client, ctx = isolation_ctx
    token = await _mint(factory, tenant_id=ctx["tenant_a"], kind=kind)
    r = await client.get(f"/api/inv/{token}")
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == kind


# ── expiry ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_an_expired_invitation_is_gone_on_read(isolation_ctx, factory, kind):
    client, ctx = isolation_ctx
    token = await _mint(factory, tenant_id=ctx["tenant_a"], kind=kind, expires_in_hours=-1)
    r = await client.get(f"/api/inv/{token}")
    assert r.status_code == 410
    assert r.json()["detail"] == "invitation_expired"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_an_expired_invitation_cannot_be_claimed(isolation_ctx, factory, kind):
    """The check must be on the CLAIM, not only on the read it is easy to skip."""
    client, ctx = isolation_ctx
    token = await _mint(factory, tenant_id=ctx["tenant_a"], kind=kind, expires_in_hours=-1)
    r = await client.post(f"/api/inv/{token}/claim", json={"init_data": "x", "locale": "uz"})
    assert r.status_code == 410, r.text
    assert r.json()["detail"] == "invitation_expired"


@pytest.mark.asyncio
async def test_expiry_is_enforced_before_telegram_verification(isolation_ctx, factory):
    """Ordering, not just presence.

    A stale-token check placed after `verify_init_data` would be unreachable for
    a caller with no valid initData — i.e. for exactly the caller it exists to
    stop. Garbage initData plus an expired token must report the EXPIRY.
    """
    client, ctx = isolation_ctx
    token = await _mint(factory, tenant_id=ctx["tenant_a"], kind="customer", expires_in_hours=-1)
    r = await client.post(f"/api/inv/{token}/claim",
                          json={"init_data": "obviously-not-a-signature", "locale": "uz"})
    assert r.status_code == 410
    assert r.json()["detail"] == "invitation_expired"   # not 401 init_data_invalid


# ── single use ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_consumed_invitation_is_gone_on_read(isolation_ctx, factory, kind):
    client, ctx = isolation_ctx
    token = await _mint(factory, tenant_id=ctx["tenant_a"], kind=kind, used=True)
    r = await client.get(f"/api/inv/{token}")
    assert r.status_code == 410
    assert r.json()["detail"] == "invitation_already_used"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_consumed_invitation_cannot_be_claimed_again(isolation_ctx, factory, kind):
    client, ctx = isolation_ctx
    token = await _mint(factory, tenant_id=ctx["tenant_a"], kind=kind, used=True)
    r = await client.post(f"/api/inv/{token}/claim", json={"init_data": "x", "locale": "uz"})
    assert r.status_code == 410
    assert r.json()["detail"] == "invitation_invalid_or_used"


@pytest.mark.asyncio
async def test_reuse_is_refused_before_telegram_verification(isolation_ctx, factory):
    client, ctx = isolation_ctx
    token = await _mint(factory, tenant_id=ctx["tenant_a"], kind="customer", used=True)
    r = await client.post(f"/api/inv/{token}/claim",
                          json={"init_data": "obviously-not-a-signature", "locale": "uz"})
    assert r.status_code == 410
    assert r.json()["detail"] == "invitation_invalid_or_used"


# ── unknown tokens ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_unknown_token_is_not_found_on_read(isolation_ctx):
    client, _ = isolation_ctx
    r = await client.get(f"/api/inv/tk{uuid4().hex}")
    assert r.status_code == 404
    assert r.json()["detail"] == "invitation_not_found"


@pytest.mark.asyncio
async def test_an_unknown_token_cannot_be_claimed(isolation_ctx):
    client, _ = isolation_ctx
    r = await client.post(f"/api/inv/tk{uuid4().hex}/claim",
                          json={"init_data": "x", "locale": "uz"})
    assert r.status_code == 410
    assert r.json()["detail"] == "invitation_invalid_or_used"


# ── revocation ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_revoked_invitation_stops_working(isolation_ctx, factory, kind):
    from sqlalchemy import select

    from app.models import Invitation

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    token = await _mint(factory, tenant_id=ctx["tenant_a"], kind=kind)

    agen = factory()
    db = await agen.__anext__()
    try:
        inv_id = (await db.execute(
            select(Invitation.id).where(Invitation.token == token)
        )).scalar_one()
    finally:
        await agen.aclose()

    assert (await client.delete(f"/api/tenant/invitations/{inv_id}", headers=auth)).status_code == 204
    assert (await client.get(f"/api/inv/{token}")).status_code == 404
    claim = await client.post(f"/api/inv/{token}/claim", json={"init_data": "x", "locale": "uz"})
    assert claim.status_code == 410


@pytest.mark.asyncio
async def test_another_tenant_cannot_revoke_this_tenants_invitation(isolation_ctx, factory):
    """Tenant isolation on the revoke path — a 404, not a 403, so the endpoint
    does not confirm that someone else's invitation id exists."""
    from sqlalchemy import select

    from app.models import Invitation

    client, ctx = isolation_ctx
    token = await _mint(factory, tenant_id=ctx["tenant_a"], kind="staff", role="sales")

    agen = factory()
    db = await agen.__anext__()
    try:
        inv_id = (await db.execute(
            select(Invitation.id).where(Invitation.token == token)
        )).scalar_one()
    finally:
        await agen.aclose()

    auth_b = {**ctx["auth_b"], "X-Tenant-Id": ctx["tenant_b"]}
    r = await client.delete(f"/api/tenant/invitations/{inv_id}", headers=auth_b)
    assert r.status_code == 404, r.text
    # And tenant A's invitation is untouched.
    assert (await client.get(f"/api/inv/{token}")).status_code == 200


# ── the gap this pass found ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_platform_admin_invitation_cannot_be_revoked_by_any_tenant(
    isolation_ctx, factory,
):
    """DOCUMENTS A GAP — this is the current behaviour, not the desired one.

    A platform-admin invitation carries `tenant_id = NULL`, and the only revoke
    route filters `Invitation.tenant_id == tenant.id`. So the highest-privilege
    invitation in the product has no revoke path at all: once minted it stays
    claimable until it expires, and a link sent to the wrong person cannot be
    withdrawn.

    Asserted rather than fixed because the fix is a new platform-scoped route
    plus its own super-admin authorization, which belongs with the invite work
    in #12 rather than bolted onto a tenant endpoint. When that route lands,
    this test should be inverted, not deleted.
    """
    from sqlalchemy import select

    from app.models import Invitation

    client, ctx = isolation_ctx
    token = await _mint(factory, tenant_id=None, kind="platform_admin", role="super_admin")

    agen = factory()
    db = await agen.__anext__()
    try:
        inv_id = (await db.execute(
            select(Invitation.id).where(Invitation.token == token)
        )).scalar_one()
    finally:
        await agen.aclose()

    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    r = await client.delete(f"/api/tenant/invitations/{inv_id}", headers=auth)
    assert r.status_code == 404, "a tenant route unexpectedly reached a platform invite"

    # Still live — which is the gap.
    assert (await client.get(f"/api/inv/{token}")).status_code == 200

    # Expiry remains the ONLY thing that stops it.
    from app.models import Invitation as I2
    agen = factory()
    db = await agen.__anext__()
    try:
        inv = (await db.execute(select(I2).where(I2.token == token))).scalar_one()
        inv.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    finally:
        await agen.aclose()
    assert (await client.get(f"/api/inv/{token}")).status_code == 410


# ── owner invite — full claim lifecycle (M-4, MUDD-70) ──────────────────────
#
# Every KINDS-parametrized case above stops at a PRE-verification rejection
# path (expiry/reuse/unknown token) — none of them reach a successful claim.
# The one test that names a concrete role uses role="sales" (the revocation
# test above). role="owner" is the store owner's own first-login path and the
# highest-privilege staff invitation there is, and it was entirely untested
# through to completion. This drives it end to end: mint -> claim ->
# TenantUser materialised with role=owner, invitation marked used, and reuse
# refused — using migrated_db + a monkeypatched verify_init_data (the same
# HMAC-bypass pattern as test_platform_admin_claim_audit.py) so the assertion
# is about the claim/membership logic, not about forging a Telegram signature.

async def test_an_owner_staff_invitation_claims_through_to_membership(
    migrated_db, monkeypatch,
) -> None:
    from types import SimpleNamespace
    from uuid import UUID as _UUID

    from fastapi import HTTPException
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.api.invitations import claim_invitation
    from app.models import Invitation, Tenant, TenantUser, User
    from app.models.enums import TenantRole

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        # A fresh trial tenant has 0 active staff and the demo plan's max_staff
        # is 1, so this single claim fits under the cap without an override.
        t = Tenant(slug="owner-inv-co", name="Owner Inv Co")
        s.add(t)
        await s.flush()
        tenant_id = t.id
        s.add(Invitation(
            token="tok-owner-claim", tenant_id=tenant_id, kind="staff", role="owner",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        await s.commit()

    # The handler imports verify_init_data at call time from this module, so
    # patching it here wins (mirrors test_platform_admin_claim_audit.py).
    tg_user = SimpleNamespace(
        id=889900, username="newowner", first_name="New", last_name="Owner", photo_url=None,
    )
    import app.core.telegram_auth as tgauth
    monkeypatch.setattr(tgauth, "verify_init_data", lambda *a, **k: SimpleNamespace(user=tg_user))

    # Staff claims require a proved phone but not market eligibility (see the
    # note beside the customer gate in invitations.py) — establish the proof
    # exactly as the bot's contact-share does it.
    async with Session() as s:
        from app.services.telegram_contact import record_verified_contact

        s.add(User(telegram_id=889900))
        await s.flush()
        res = await record_verified_contact(
            s, sender_telegram_id=889900, contact_user_id=889900, raw_phone="+998901112299",
        )
        assert res.ok, res.outcome
        await s.commit()

    async with Session() as s:
        result = await claim_invitation(
            token="tok-owner-claim",
            body={"init_data": "stub", "first_name": "New", "last_name": "Owner"},
            db=s,
        )
    assert result["ok"] is True
    assert result["kind"] == "staff"
    assert result["role"] == "owner"

    async with Session() as s:
        membership = (await s.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == _UUID(result["user_id"]),
            )
        )).scalar_one()
        assert membership.role == TenantRole.owner
        assert membership.is_active is True

        claimed = (await s.execute(
            select(Invitation).where(Invitation.token == "tok-owner-claim")
        )).scalar_one()
        assert claimed.used_at is not None

    # Single-use: a second claim of the same token is refused outright, not a
    # second owner seat for the tenant.
    async with Session() as s:
        with pytest.raises(HTTPException) as ei:
            await claim_invitation(
                token="tok-owner-claim",
                body={"init_data": "stub", "first_name": "New"},
                db=s,
            )
    assert ei.value.status_code == 410
