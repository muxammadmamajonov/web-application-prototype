"""TR-025 — the eight obsolete Phase-2-REMOVE mutation endpoints are gone, and
everything that replaced them still works.

This is a *negative* guard, so it is the one file in the repository that must
still name the removed handlers and ``(method, path)`` pairs: they are the
assertion subjects. The zero-production-reference greps in the audit's §123.4
therefore search ``backend/app`` and ``frontend/src`` — not this file.

Two tiers, because a route table and a behaviour are different claims:

* **Route table** (no database) — the eight pairs are absent *by pair*, not by
  grep, so re-registering one under a different module is still caught; the
  protected neighbours are present *with their handler names*, so a rename is
  caught too; and the total route count is pinned independently of
  ``test_tr005``'s ``TOTAL_APP_ROUTES``.
* **Behaviour** (``migrated_db``, real schema) — each removed route had a
  retained replacement or a protection that the removal must not weaken. A
  route-table test cannot tell whether cancellation still preserves the record,
  or whether the last-owner guard still fires; these do.

The full invitation *claim* handshake needs signed Telegram initData and is
already covered by ``test_invitation_claim_validation.py`` and
``test_platform_admin_claim_audit.py``; the plan cap on the claim path is
covered by ``test_staff_cap.py``. This file asserts that the invitation those
tests consume is still minted with the role baked in, rather than duplicating
their fixtures.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.routing import APIRoute
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import invite_codes
from app.models import AuditLog, CatalogColor, Customer, Invitation, Plan, Tenant, TenantUser, User
from app.models.enums import (
    CustomerStatus,
    InstallmentStatus,
    PlatformRole,
    TenantRole,
)

# ── the deletion set — audit §121, verbatim ────────────────────────────────

#: The eight unique (method, path) pairs removed by TR-025.
REMOVED: tuple[tuple[str, str], ...] = (
    ('DELETE', '/api/platform/audit'),
    ('POST',   '/api/catalog/colors'),
    ('PATCH',  '/api/catalog/colors/{color_id}'),
    ('DELETE', '/api/catalog/colors/{color_id}'),
    ('PATCH',  '/api/tenant/installments/{iid}'),
    ('DELETE', '/api/tenant/installments/{iid}'),
    ('POST',   '/api/tenant/staff'),
    ('POST',   '/api/platform/admins'),
)

#: The handler functions deleted with them.
REMOVED_HANDLERS: frozenset[str] = frozenset({
    'clear_audit',
    'create_color',
    'update_color',
    'delete_color',
    'update_installment',
    'delete_installment',
    'invite_staff',
    'invite_admin',
})

#: Total APIRoutes after the removal. Pinned here independently of
#: test_tr005.TOTAL_APP_ROUTES so the two must agree by arithmetic, not by
#: importing one from the other.
#: 194 -> 193: `GET /demo/intent/{slug}` was removed with the self-serve demo
#: provisioning it existed to serve (see app/core/scope.py).
#: 193 -> 195: `POST /api/downloads/grant` and `GET /api/dl/{kind}/{ident}`
#: were added by #10 — the signed-download pair that lets a Telegram WebView
#: open a contract PDF at all. `/dl` is deliberately UNAUTHENTICATED: its HMAC
#: signature is the capability, which is exactly why it is worth counting here.
# 196 since GET /contact/channels: the support handle and corporate
# email are configured on Sozlamalar -> Aloqa kanali, and nothing read
# them back — every user-facing mention was a frontend constant, so the
# settings form was a set of fields whose values went nowhere.
#: +2 for the identity surface: the server-owned verification state
#: (GET /api/me/identity) and the invitation prefill (POST /api/inv/{token}/identity).
#: 200 -> 202: MUDD-134 added the owner-dashboard analytics pair
#: (GET /api/tenant/analytics/summary, GET /api/tenant/analytics/sales-by-category).
TOTAL_APP_ROUTES_AFTER_TR025 = 202

#: Retained replacements and neighbours — audit §120 / §123.2. A removal that
#: took one of these with it, renamed it, or moved it to another method must
#: fail here.
PROTECTED: tuple[tuple[str, str, str], ...] = (
    ('GET',    '/api/catalog/colors',                          'list_colors'),
    ('POST',   '/api/catalog/categories',                      'create_category'),
    ('PATCH',  '/api/catalog/categories/{cat_id}',             'update_category'),
    ('DELETE', '/api/catalog/categories/{cat_id}',             'delete_category'),
    ('GET',    '/api/platform/audit',                          'list_audit'),
    ('GET',    '/api/platform/admins',                         'list_admins'),
    ('PATCH',  '/api/platform/admins/{user_id}',               'update_admin'),
    ('DELETE', '/api/platform/admins/{user_id}',               'remove_admin'),
    ('POST',   '/api/platform/admin-invitations',              'create_admin_invitation'),
    ('GET',    '/api/tenant/staff',                            'list_staff'),
    ('PATCH',  '/api/tenant/staff/{tu_id}',                    'update_staff'),
    ('DELETE', '/api/tenant/staff/{tu_id}',                    'revoke_staff'),
    ('POST',   '/api/tenant/invitations',                      'create_invitation'),
    ('GET',    '/api/tenant/invitations',                      'list_invitations'),
    ('DELETE', '/api/tenant/invitations/{invite_id}',          'revoke_invitation'),
    ('POST',   '/api/inv/{token}/claim',                       'claim_invitation'),
    ('POST',   '/api/tenant/installments',                     'create_installment'),
    ('PUT',    '/api/tenant/installments/{iid}',               'replace_installment'),
    ('POST',   '/api/tenant/installments/{iid}/cancel',        'cancel_installment'),
    ('POST',   '/api/tenant/installments/{iid}/reschedule',    'reschedule_installment'),
)


def _api_routes() -> list[APIRoute]:
    from app.main import app

    return [r for r in app.routes if isinstance(r, APIRoute)]


def _live_pairs() -> set[tuple[str, str]]:
    return {(sorted(r.methods)[0], r.path) for r in _api_routes()}


# ── tier 1: route table ────────────────────────────────────────────────────

def test_removal_set_is_exactly_eight_unique_pairs() -> None:
    """The set itself is the contract — no duplicate may creep back in, and the
    count may not drift as the tuple is edited."""
    assert len(REMOVED) == 8
    assert len(set(REMOVED)) == 8
    assert len(REMOVED_HANDLERS) == 8


def test_removed_pairs_are_absent() -> None:
    """By (method, path) against the live table, so re-registration under any
    module — not just the six the deletion touched — is caught."""
    live = _live_pairs()
    still_there = [p for p in REMOVED if p in live]
    assert still_there == [], f"TR-025 routes are back on the app: {still_there}"


def test_removed_handler_names_are_absent() -> None:
    """A handler kept under a new path is still the removed capability."""
    names = {r.name for r in _api_routes()}
    resurrected = sorted(names & REMOVED_HANDLERS)
    assert resurrected == [], f"removed handlers still registered: {resurrected}"


def test_total_route_count() -> None:
    """202 - 3 catalog - 2 platform - 3 tenant = 194, - 1 demo-intent peek = 193,
    + 5 later additions, + 2 identity endpoints = 198."""
    assert len(_api_routes()) == TOTAL_APP_ROUTES_AFTER_TR025


def test_protected_neighbours_present() -> None:
    """Every retained replacement is live, at its path, under its own name."""
    live = {(sorted(r.methods)[0], r.path): r.name for r in _api_routes()}
    missing = [(m, p, n) for (m, p, n) in PROTECTED if live.get((m, p)) != n]
    assert missing == [], f"protected routes missing or renamed: {missing}"


def test_colors_path_retains_only_its_read() -> None:
    """The colours path survives as a read. Stated separately from the pair
    assertions because 'the palette is still readable' is the actual product
    guarantee behind CATALOG-004."""
    live = _live_pairs()
    assert ('GET', '/api/catalog/colors') in live
    colour_methods = {m for (m, p) in live if p.startswith('/api/catalog/colors')}
    assert colour_methods == {'GET'}


def test_installments_path_retains_its_replacement_workflows() -> None:
    """PATCH and DELETE are gone from /installments/{iid}; PUT and GET stay, and
    cancel/reschedule stay on their own sub-paths."""
    live = _live_pairs()
    methods = {m for (m, p) in live if p == '/api/tenant/installments/{iid}'}
    assert methods == {'GET', 'PUT'}


# ── tier 2: behaviour of the retained replacements (audit §123.3) ──────────

async def _seed(session) -> tuple[Tenant, User, TenantUser, Customer]:
    """A tenant with an owner and an approved customer, on a plan roomy enough
    for the staff/contract caps these tests cross."""
    session.add(Plan(code="tr025", name_uz="TR025", name_ru="TR025",
                     price_monthly=0, max_staff=10))
    tenant = Tenant(slug="tr025-shop", name="TR025 Shop", plan_code="tr025")
    session.add(tenant)
    await session.flush()

    owner_user = User(telegram_id=770001, first_name="Owner", is_active=True)
    session.add(owner_user)
    await session.flush()

    membership = TenantUser(tenant_id=tenant.id, user_id=owner_user.id,
                            role=TenantRole.owner, is_active=True)
    customer = Customer(tenant_id=tenant.id, full_name="Payer P",
                        phone="+998901110001", status=CustomerStatus.active)
    session.add_all([membership, customer])
    await session.flush()
    await session.commit()
    return tenant, owner_user, membership, customer


async def _new_contract(session, tenant, owner_user, membership, customer, *, months: int = 6) -> dict:
    from app.api.tenant.installments import InstallmentIn, InstallmentItemIn, create_installment

    return await create_installment(
        body=InstallmentIn(
            customer_id=customer.id, months=months,
            items=[InstallmentItemIn(product_name_snapshot="Phone",
                                     unit_price=Decimal("6000000"), qty=1)],
        ),
        user=owner_user, ctx=(tenant, membership), db=session,
    )


async def _audit_actions(session, tenant_id, entity_id: str) -> set[str]:
    rows = (await session.execute(
        select(AuditLog.action).where(
            AuditLog.tenant_id == tenant_id, AuditLog.entity_id == entity_id,
        )
    )).scalars().all()
    return set(rows)


async def test_cancellation_replaces_contract_delete(migrated_db) -> None:
    """DELETE destroyed the row. Cancel preserves it — that is the whole point
    of the CONTRACT-019 REMOVE decision, so assert the record survives."""
    from app.api.tenant.installments import CancelIn, cancel_installment
    from app.models import Installment

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        tenant, owner_user, membership, customer = await _seed(s)
        created = await _new_contract(s, tenant, owner_user, membership, customer)
        iid = UUID(created["id"])

        result = await cancel_installment(
            iid=iid, body=CancelIn(reason="customer withdrew"),
            user=owner_user, ctx=(tenant, membership), db=s,
        )
        assert result["status"] == InstallmentStatus.cancelled.value

    async with Session() as s:
        row = (await s.execute(select(Installment).where(Installment.id == iid))).scalar_one_or_none()
        assert row is not None, "cancel must preserve the financial record, not delete it"
        assert row.status is InstallmentStatus.cancelled
        assert row.closed_at is not None
        assert "installment.cancel" in await _audit_actions(s, tenant.id, str(iid))


async def test_cancelled_contract_rejects_a_new_manual_payment(migrated_db) -> None:
    """Cancellation is terminal: recording money must not resurrect the loan."""
    from app.api.tenant.installments import CancelIn, cancel_installment
    from app.api.tenant.payments import PaymentIn, create_payment
    from app.models import Installment, Payment

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        tenant, owner_user, membership, customer = await _seed(s)
        created = await _new_contract(s, tenant, owner_user, membership, customer)
        iid = UUID(created["id"])
        await cancel_installment(
            iid=iid, body=CancelIn(reason="customer withdrew"),
            user=owner_user, ctx=(tenant, membership), db=s,
        )

        with pytest.raises(HTTPException) as exc:
            await create_payment(
                body=PaymentIn(installment_id=iid, amount=Decimal("100000"),
                               method="cash", mark_approved=True),
                user=owner_user, ctx=(tenant, membership), db=s,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "installment_closed"

    async with Session() as s:
        inst = await s.get(Installment, iid)
        count = (await s.execute(
            select(func.count(Payment.id)).where(Payment.installment_id == iid)
        )).scalar_one()
        assert inst.status is InstallmentStatus.cancelled
        assert inst.paid_amount == 0
        assert count == 0


async def test_pending_manual_payment_cannot_be_approved_after_cancellation(migrated_db) -> None:
    """A sales submission may outlive the contract; approval must re-check it."""
    from app.api.tenant.installments import CancelIn, cancel_installment
    from app.api.tenant.payments import PaymentIn, approve_payment, create_payment
    from app.models import Installment, Payment
    from app.models.enums import PaymentStatus

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        tenant, owner_user, membership, customer = await _seed(s)
        created = await _new_contract(s, tenant, owner_user, membership, customer)
        iid = UUID(created["id"])
        pending = await create_payment(
            body=PaymentIn(installment_id=iid, amount=Decimal("100000"),
                           method="cash", mark_approved=False),
            user=owner_user, ctx=(tenant, membership), db=s,
        )
        await cancel_installment(
            iid=iid, body=CancelIn(reason="customer withdrew"),
            user=owner_user, ctx=(tenant, membership), db=s,
        )

        with pytest.raises(HTTPException) as exc:
            # Called directly rather than over HTTP, so the background-task
            # collector FastAPI would inject is supplied by hand.
            await approve_payment(
                payment_id=UUID(pending["id"]), background=BackgroundTasks(),
                user=owner_user, ctx=(tenant, membership), db=s,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "installment_closed"

    async with Session() as s:
        inst = await s.get(Installment, iid)
        payment = await s.get(Payment, UUID(pending["id"]))
        assert inst.status is InstallmentStatus.cancelled
        assert inst.paid_amount == 0
        assert payment.status is PaymentStatus.pending


async def test_reschedule_replaces_the_status_and_date_patch(migrated_db) -> None:
    """The removed PATCH set status/next_due_date by hand with no lock, no guard
    and no audit row. Reschedule re-derives the schedule and audits it."""
    from app.api.tenant.installments import RescheduleIn, reschedule_installment
    from app.models import Installment

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        tenant, owner_user, membership, customer = await _seed(s)
        created = await _new_contract(s, tenant, owner_user, membership, customer, months=6)
        iid = UUID(created["id"])
        before_months = created["months"]

        result = await reschedule_installment(
            iid=iid, body=RescheduleIn(add_months=3),
            user=owner_user, ctx=(tenant, membership), db=s,
        )
        assert result["months"] == before_months + 3

    async with Session() as s:
        row = (await s.execute(select(Installment).where(Installment.id == iid))).scalar_one()
        assert row.months == before_months + 3
        assert row.next_due_date is not None
        assert "installment.reschedule" in await _audit_actions(s, tenant.id, str(iid))


async def test_replacement_editor_is_intact_and_still_guards_payments(migrated_db) -> None:
    """PUT is the retained full edit. Its `has_payments` guard is what made the
    unguarded PATCH redundant, so both halves are asserted."""
    from app.api.tenant.installments import (
        InstallmentIn,
        InstallmentItemIn,
        replace_installment,
    )
    from app.api.tenant.payments import PaymentIn, create_payment

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        tenant, owner_user, membership, customer = await _seed(s)
        created = await _new_contract(s, tenant, owner_user, membership, customer)
        iid = UUID(created["id"])

        body = InstallmentIn(
            customer_id=customer.id, months=9,
            items=[InstallmentItemIn(product_name_snapshot="Phone XL",
                                     unit_price=Decimal("9000000"), qty=1)],
        )
        edited = await replace_installment(
            iid=iid, body=body, user=owner_user, ctx=(tenant, membership), db=s,
        )
        assert edited["months"] == 9

        await create_payment(
            body=PaymentIn(installment_id=iid, amount=Decimal("1000000"),
                           method="cash", mark_approved=True),
            user=owner_user, ctx=(tenant, membership), db=s,
        )

        with pytest.raises(HTTPException) as exc:
            await replace_installment(
                iid=iid, body=body, user=owner_user, ctx=(tenant, membership), db=s,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "has_payments"


async def test_retention_replaces_the_audit_purge(migrated_db) -> None:
    """OPS-006's wipe deleted every audit row unconditionally. The retention job
    is age-bounded: aged rows go, in-window rows stay."""
    from app.services.scheduler import _AUDIT_RETENTION_DAYS, _purge_expired

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    now = datetime.now(UTC)
    async with Session() as s:
        tenant, *_ = await _seed(s)
        s.add_all([
            AuditLog(tenant_id=tenant.id, action="aged.row", entity_type="test",
                     entity_id="aged",
                     created_at=now - timedelta(days=_AUDIT_RETENTION_DAYS + 5)),
            AuditLog(tenant_id=tenant.id, action="fresh.row", entity_type="test",
                     entity_id="fresh", created_at=now - timedelta(days=1)),
        ])
        await s.commit()

        await _purge_expired(s, now=now)

    async with Session() as s:
        surviving = set((await s.execute(
            select(AuditLog.action).where(AuditLog.entity_type == "test")
        )).scalars().all())
        assert "aged.row" not in surviving, "retention must age out rows past the window"
        assert "fresh.row" in surviving, "retention must not touch in-window rows"


async def test_invitation_link_replaces_staff_seeding(migrated_db) -> None:
    """ROLE-010 seeded a TenantUser straight from a raw Telegram ID. The retained
    path mints a single-use link with the role baked into the row — which is what
    the claim-flow tests then consume."""
    from app.api.invitations import CreateInvitationIn, create_invitation

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        tenant, _owner, membership, _customer = await _seed(s)
        out = await create_invitation(
            body=CreateInvitationIn(kind="staff", role="sales"),
            ctx=(tenant, membership), db=s,
        )
        assert out.token
        assert out.role == "sales"

    async with Session() as s:
        inv = (await s.execute(
            select(Invitation).where(invite_codes.lookup_filter(out.token))
        )).scalar_one()
        assert inv.kind == "staff"
        assert inv.role == "sales", "the role must be baked into the row, not chosen by the claimer"
        assert inv.tenant_id == tenant.id
        assert inv.expires_at > datetime.now(UTC)


async def test_owner_role_ceiling_still_blocks_a_manager(migrated_db) -> None:
    """The comment that referenced the deleted /staff endpoint guarded this. The
    guard itself must remain — it is now the only staff-creation ceiling."""
    from app.api.invitations import CreateInvitationIn, create_invitation

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        tenant, _owner, _membership, _customer = await _seed(s)
        mgr_user = User(telegram_id=770009, first_name="Mgr", is_active=True)
        s.add(mgr_user)
        await s.flush()
        mgr = TenantUser(tenant_id=tenant.id, user_id=mgr_user.id,
                         role=TenantRole.manager, is_active=True)
        s.add(mgr)
        await s.commit()

        with pytest.raises(HTTPException) as exc:
            await create_invitation(
                body=CreateInvitationIn(kind="staff", role="owner"),
                ctx=(tenant, mgr), db=s,
            )
        assert exc.value.status_code == 403
        assert exc.value.detail == "forbidden_role_escalation"


async def test_staff_management_and_last_owner_protection_intact(migrated_db) -> None:
    """GET/PATCH/DELETE staff and the last-owner guard are the KEEP half of
    ROLE-010's removal; none of them may have been weakened."""
    from app.api.tenant_settings import StaffPatch, revoke_staff, update_staff

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        tenant, _owner, membership, _customer = await _seed(s)
        sales_user = User(telegram_id=770002, first_name="Sales", is_active=True)
        s.add(sales_user)
        await s.flush()
        sales = TenantUser(tenant_id=tenant.id, user_id=sales_user.id,
                           role=TenantRole.sales, is_active=True)
        s.add(sales)
        await s.commit()
        sales_id = sales.id

        promoted = await update_staff(
            tu_id=sales_id, body=StaffPatch(role="manager"),
            ctx=(tenant, membership), db=s,
        )
        assert promoted["role"] == "manager"

        # The sole owner may not be removed.
        with pytest.raises(HTTPException) as exc:
            await revoke_staff(tu_id=membership.id, ctx=(tenant, membership), db=s)
        assert exc.value.status_code == 409
        assert exc.value.detail == "cannot_remove_last_owner"

        # …nor deactivated.
        with pytest.raises(HTTPException) as exc:
            await update_staff(
                tu_id=membership.id, body=StaffPatch(is_active=False),
                ctx=(tenant, membership), db=s,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail == "cannot_deactivate_last_owner"

        # A non-owner member still revokes cleanly.
        await revoke_staff(tu_id=sales_id, ctx=(tenant, membership), db=s)

    async with Session() as s:
        assert (await s.execute(
            select(TenantUser).where(TenantUser.id == sales_id)
        )).scalar_one_or_none() is None


async def test_admin_link_replaces_by_telegram_id_promotion(migrated_db) -> None:
    """MISC-008 promoted a user to super_admin outright and wrote no audit row.
    The retained link is single-use, expiring, role-baked and audited."""
    from app.api.platform.provisioning import AdminInviteLinkIn, create_admin_invitation

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        admin = User(telegram_id=880001, platform_role=PlatformRole.super_admin, is_active=True)
        s.add(admin)
        await s.commit()

        out = await create_admin_invitation(
            body=AdminInviteLinkIn(role="support", expires_hours=72), user=admin, db=s,
        )
        for key in ("token", "telegram_url", "web_url", "expires_at"):
            assert out.get(key), f"admin invitation must return {key}"

    async with Session() as s:
        inv = (await s.execute(
            select(Invitation).where(invite_codes.lookup_filter(out["token"]))
        )).scalar_one()
        assert inv.kind == "platform_admin"
        assert inv.role == "support", "the role must be baked into the token row"
        assert inv.tenant_id is None
        assert inv.expires_at > datetime.now(UTC)

        actions = set((await s.execute(
            select(AuditLog.action).where(AuditLog.entity_id == str(inv.id))
        )).scalars().all())
        assert "admin_invitation.create" in actions

        # The removed endpoint minted admins directly; nothing may have promoted
        # a user as a side effect of minting a link.
        promoted = (await s.execute(
            select(func.count(User.id)).where(User.platform_role.is_not(None))
        )).scalar_one()
        assert promoted == 1, "minting an invitation must not itself create an admin"


async def test_admin_self_protections_intact(migrated_db) -> None:
    """GET/PATCH/DELETE admins survive with their lock-out protections."""
    from app.api.platform.directory import AdminUpdateIn, remove_admin, update_admin

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        admin = User(telegram_id=880002, platform_role=PlatformRole.super_admin, is_active=True)
        s.add(admin)
        await s.commit()

        with pytest.raises(HTTPException) as exc:
            await update_admin(user_id=str(admin.id), body=AdminUpdateIn(role="support"),
                               user=admin, db=s)
        assert exc.value.status_code == 400
        assert exc.value.detail == "cannot_demote_self"

        with pytest.raises(HTTPException) as exc:
            await remove_admin(user_id=str(admin.id), user=admin, db=s)
        assert exc.value.status_code == 400
        assert exc.value.detail == "cannot_remove_self"


async def test_colour_palette_is_still_readable(migrated_db) -> None:
    """CATALOG-004 removed the colour *administration*, not the palette. The
    seeded rows survive the deploy and the public read still serves them."""
    from app.api.catalog import list_colors

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        stored = (await s.execute(
            select(func.count(CatalogColor.id)).where(CatalogColor.is_active.is_(True))
        )).scalar_one()
        assert stored > 0, "the migration-seeded palette must survive the removal"

        served = await list_colors(db=s)
        assert isinstance(served, list)
        assert len(served) == stored, "the read must still serve every active colour"


async def test_category_attribute_schema_editing_is_intact(migrated_db) -> None:
    """The retained replacement for colours: a variant axis is expressed through
    the category attribute schema, so that editor must still work."""
    from app.api.catalog import CategoryIn, create_category, update_category

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        admin = User(telegram_id=880003, platform_role=PlatformRole.super_admin, is_active=True)
        s.add(admin)
        await s.commit()

        created = await create_category(
            body=CategoryIn(slug="tr025-cat", name_uz="Kategoriya", name_ru="Категория"),
            user=admin, db=s,
        )
        updated = await update_category(
            cat_id=created["id"],
            body=CategoryIn(
                slug="tr025-cat", name_uz="Kategoriya", name_ru="Категория",
                attribute_schema=[{"key": "color", "type": "select",
                                   "options": ["red", "blue"]}],
                identifier_schema=[{"key": "imei", "type": "text"}],
            ),
            user=admin, db=s,
        )
        assert updated["attribute_schema"][0]["key"] == "color"
        assert updated["identifier_schema"][0]["key"] == "imei"


def test_removed_writer_of_draft_status_is_gone() -> None:
    """`delete_installment` was draft-only and the removed PATCH was the only
    writer of `draft`. Neither remains, so the audit's claim that the delete was
    already unreachable now holds unconditionally."""
    names = {r.name for r in _api_routes()}
    assert 'update_installment' not in names
    assert 'delete_installment' not in names
    assert InstallmentStatus.draft.value == "draft"  # the enum member itself stays


def test_deleted_request_models_are_gone() -> None:
    """The request models died with their handlers; nothing may still expose
    them, or a future handler could quietly re-adopt the removed contract."""
    from app.api import catalog as catalog_mod
    from app.api import tenant_settings as settings_mod
    from app.api.platform import provisioning as provisioning_mod
    from app.api.tenant import installments as installments_mod

    assert not hasattr(catalog_mod, "ColorIn")
    assert not hasattr(installments_mod, "InstallmentPatch")
    assert not hasattr(settings_mod, "StaffInviteIn")
    assert not hasattr(provisioning_mod, "AdminInviteIn")


def test_shared_owner_guard_survives_its_last_local_caller() -> None:
    """`_require_owner` was narrowed out of the installments module, not deleted:
    billing and customers still depend on the shared helper."""
    from app.api.tenant import _shared, billing, customers
    from app.api.tenant import installments as installments_mod

    assert callable(_shared._require_owner)
    assert not hasattr(installments_mod, "_require_owner"), (
        "the installments module must no longer import the owner guard"
    )
    assert billing._require_owner is _shared._require_owner
    assert customers._require_owner is _shared._require_owner


def test_reschedule_still_accepts_an_explicit_due_date() -> None:
    """The removed PATCH's only non-redundant field was `next_due_date`. The
    retained reschedule payload still carries it, so nothing was lost."""
    from app.api.tenant.installments import RescheduleIn

    body = RescheduleIn(add_months=1, next_due_date=date(2030, 1, 1))
    assert body.add_months == 1
    assert body.next_due_date == date(2030, 1, 1)
