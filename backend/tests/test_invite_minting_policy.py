"""Who may mint an invitation — the allow AND deny matrix, over real HTTP.

An invitation is a bearer credential: whoever holds the link binds a Telegram
account to a customer record, or takes a back-office seat with the role the
invite carries. Batch 4 recorded that ``resend_customer_invite`` and
``create_customer`` "share a role guard that admits sales staff". Reading the
code in Batch 6 found something narrower and worse: **they shared no guard at
all.** Neither called ``_require_owner_or_manager`` nor anything else, so any
member of the tenant could mint a live bearer credential, and nothing recorded
that they had.

These tests drive the real dependency chain with real tokens and real roles.
They are deliberately written as a *matrix* rather than as one regression case,
because the question the batch asks is not "is the bug fixed" but "is the policy
enforced" — and a policy is a table, not a branch.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

from app.core.invite_policy import (
    GRANTED_ROLE_CEILING,
    TENANT_MINT_ALLOWED,
    MintSurface,
    require_may_mint,
)
from app.models.enums import TenantRole

pytestmark = pytest.mark.asyncio

CUSTOMERS = "/api/tenant/customers"
INVITATIONS = "/api/tenant/invitations"


@pytest_asyncio.fixture
async def roles_ctx():
    """One tenant, one member per role, real tokens — plus a second tenant."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Customer, Tenant, TenantUser, User

    engine = create_async_engine(url)
    # DROP SCHEMA rather than metadata.drop_all: the fail-closed RLS policies
    # reference OTHER tables (`users` is filtered through `tenant_users`), and
    # drop_all cannot order those drops — it fails with DependentObjectsStillExist
    # whenever a previous test left the migrated schema behind. Order-dependent,
    # so it is a flake rather than a failure, which is worse.
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            t = Tenant(slug="mint-a", name="Mint A")
            other = Tenant(slug="mint-b", name="Mint B")
            s.add_all([t, other])
            await s.flush()
            owner, manager, sales, outsider = (
                User(telegram_id=310001), User(telegram_id=310002),
                User(telegram_id=310003), User(telegram_id=310004),
            )
            s.add_all([owner, manager, sales, outsider])
            await s.flush()
            s.add_all([
                TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner),
                TenantUser(tenant_id=t.id, user_id=manager.id, role=TenantRole.manager),
                TenantUser(tenant_id=t.id, user_id=sales.id, role=TenantRole.sales),
                TenantUser(tenant_id=other.id, user_id=outsider.id,
                           role=TenantRole.owner),
            ])
            mine = Customer(tenant_id=t.id, full_name="Mine", phone="+998900003001")
            theirs = Customer(tenant_id=other.id, full_name="Theirs",
                              phone="+998900003002")
            s.add_all([mine, theirs])
            await s.commit()
            ctx = {
                "owner": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
                "manager": {"Authorization": f"Bearer {issue_token(user_id=str(manager.id))}"},
                "sales": {"Authorization": f"Bearer {issue_token(user_id=str(sales.id))}"},
                "outsider": {"Authorization": f"Bearer {issue_token(user_id=str(outsider.id))}"},
                "customer_id": str(mine.id),
                "other_customer_id": str(theirs.id),
                "tenant_id": str(t.id),
                "other_tenant_id": str(other.id),
                "sales_user_id": str(sales.id),
            }
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t",
        ) as client:
            yield client, ctx, Session
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()


def _customer_body(phone: str) -> dict:
    return {"full_name": "Walk In", "phone": phone}


# ── the policy table itself ──────────────────────────────────────────────────

class TestThePolicyTable:
    """Unit-level, so the table's shape is pinned independently of any route."""

    @pytest.mark.parametrize("role", list(TenantRole))
    def test_every_role_has_an_explicit_verdict_for_every_kind(self, role) -> None:
        """No kind may be silently permitted by omission."""
        class _M:
            pass

        m = _M()
        m.role = role
        for (surface, kind), allowed in TENANT_MINT_ALLOWED.items():
            if role in allowed:
                require_may_mint(m, kind, surface=surface)      # must not raise
            else:
                with pytest.raises(Exception) as e:
                    require_may_mint(m, kind, surface=surface)
                assert e.value.status_code == 403

    def test_an_unclassified_kind_is_denied(self) -> None:
        """The branch that makes a new mint site fail loudly instead of quietly."""
        class _M:
            role = TenantRole.owner

        with pytest.raises(Exception) as e:
            require_may_mint(_M(), "some_future_kind",
                             surface=MintSurface.console)
        assert e.value.detail == "invite_kind_not_permitted"

    def test_a_platform_kind_is_never_tenant_mintable(self) -> None:
        class _M:
            role = TenantRole.owner

        with pytest.raises(Exception) as e:
            require_may_mint(_M(), "platform_admin",
                             surface=MintSurface.console)
        assert e.value.detail == "invite_kind_not_tenant_mintable"

    def test_no_membership_at_all_is_denied(self) -> None:
        with pytest.raises(Exception) as e:
            require_may_mint(None, "customer", surface=MintSurface.intake)
        assert e.value.status_code == 403

    def test_the_owner_ceiling_is_the_only_ceiling(self) -> None:
        assert set(GRANTED_ROLE_CEILING) == {"owner"}
        assert GRANTED_ROLE_CEILING["owner"] == frozenset({TenantRole.owner})


# ── staff invitations: back-office authority ─────────────────────────────────

class TestTheConsoleStaysClosedToSales:
    """`POST /tenant/invitations` denied sales for every kind before Batch 6 and
    must still. The first draft of the policy table collapsed surface into kind
    and reopened it for `customer`; `test_sales_cannot_mint_or_revoke` caught it,
    and this pins the rule from the policy side too."""

    @pytest.mark.parametrize("kind", ["customer", "guarantor", "staff"])
    def test_sales_may_not_mint_any_kind_from_the_console(self, kind) -> None:
        class _M:
            role = TenantRole.sales

        with pytest.raises(Exception) as e:
            require_may_mint(_M(), kind, surface=MintSurface.console)
        assert e.value.status_code == 403

    @pytest.mark.parametrize("kind", ["customer", "guarantor"])
    def test_sales_may_still_mint_the_same_kind_at_intake(self, kind) -> None:
        class _M:
            role = TenantRole.sales

        require_may_mint(_M(), kind, surface=MintSurface.intake)   # must not raise

    def test_sales_has_no_intake_route_to_a_staff_seat(self) -> None:
        class _M:
            role = TenantRole.sales

        with pytest.raises(Exception) as e:
            require_may_mint(_M(), "staff", surface=MintSurface.intake)
        assert e.value.status_code == 403


class TestStaffInvitations:
    async def test_sales_cannot_mint_a_staff_seat(self, roles_ctx) -> None:
        client, ctx, _ = roles_ctx
        r = await client.post(
            INVITATIONS, headers=ctx["sales"],
            json={"kind": "staff", "role": "sales"},
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "invite_role_not_permitted"

    async def test_a_manager_cannot_mint_an_owner_seat(self, roles_ctx) -> None:
        """Privilege escalation: invitations are the only staff-creation path, so
        this ceiling is the only thing stopping a manager promoting themselves
        through an account they control."""
        client, ctx, _ = roles_ctx
        r = await client.post(
            INVITATIONS, headers=ctx["manager"],
            json={"kind": "staff", "role": "owner"},
        )
        assert r.status_code == 403
        assert r.json()["detail"] == "forbidden_role_escalation"

    async def test_an_owner_may_mint_an_owner_seat(self, roles_ctx) -> None:
        client, ctx, _ = roles_ctx
        r = await client.post(
            INVITATIONS, headers=ctx["owner"],
            json={"kind": "staff", "role": "owner"},
        )
        assert r.status_code == 201, r.text

    async def test_a_manager_may_mint_a_non_owner_seat(self, roles_ctx) -> None:
        client, ctx, _ = roles_ctx
        r = await client.post(
            INVITATIONS, headers=ctx["manager"],
            json={"kind": "staff", "role": "manager"},
        )
        assert r.status_code == 201, r.text


# ── customer invitations: intake ─────────────────────────────────────────────

class TestCustomerInvitations:
    """Sales KEEPS this, deliberately — see app/core/invite_policy.py. What
    changed is that the permission is now written down, checked in one place,
    and attributed."""

    async def test_sales_may_still_complete_an_intake(self, roles_ctx) -> None:
        client, ctx, _ = roles_ctx
        r = await client.post(
            CUSTOMERS, headers=ctx["sales"], json=_customer_body("+998900004001"),
        )
        assert r.status_code == 201, r.text

    async def test_sales_may_still_resend_an_invite(self, roles_ctx) -> None:
        client, ctx, _ = roles_ctx
        r = await client.post(
            f"{CUSTOMERS}/{ctx['customer_id']}/invite", headers=ctx["sales"],
        )
        assert r.status_code == 201, r.text

    async def test_every_permitted_role_can_complete_an_intake(self, roles_ctx) -> None:
        client, ctx, _ = roles_ctx
        for i, role in enumerate(("owner", "manager", "sales")):
            r = await client.post(
                CUSTOMERS, headers=ctx[role],
                json=_customer_body(f"+99890000502{i}"),
            )
            assert r.status_code == 201, f"{role}: {r.text}"


# ── the boundary the policy is not allowed to widen ──────────────────────────

class TestTenantAndReplayBoundaries:
    async def test_a_member_of_another_tenant_cannot_mint_here(self, roles_ctx) -> None:
        """Cross-tenant: the outsider owns a DIFFERENT shop. Naming our customer
        must not reach it, whatever their role is at home."""
        client, ctx, _ = roles_ctx
        r = await client.post(
            f"{CUSTOMERS}/{ctx['customer_id']}/invite", headers=ctx["outsider"],
        )
        assert r.status_code in (403, 404), r.text
        assert "token" not in r.text

    async def test_naming_another_tenants_customer_mints_nothing(self, roles_ctx) -> None:
        client, ctx, _ = roles_ctx
        r = await client.post(
            f"{CUSTOMERS}/{ctx['other_customer_id']}/invite", headers=ctx["owner"],
        )
        assert r.status_code == 404
        assert "token" not in r.text

    async def test_unauthenticated_cannot_mint(self, roles_ctx) -> None:
        client, ctx, _ = roles_ctx
        assert (await client.post(INVITATIONS, json={"kind": "staff"})).status_code == 401
        assert (await client.post(
            f"{CUSTOMERS}/{ctx['customer_id']}/invite")).status_code == 401

    async def test_a_second_resend_does_not_stack_live_credentials(
        self, roles_ctx,
    ) -> None:
        """Replay: re-issuing must not leave two usable links for one customer."""
        from datetime import UTC, datetime

        from sqlalchemy import func, select

        from app.models import Invitation

        client, ctx, Session = roles_ctx
        for _ in range(3):
            r = await client.post(
                f"{CUSTOMERS}/{ctx['customer_id']}/invite", headers=ctx["owner"],
            )
            assert r.status_code == 201, r.text

        async with Session() as s:
            # "Live" means USABLE: unused AND unexpired. Counting only unused
            # would call a superseded row live and would have passed whether or
            # not re-issuing actually replaces anything.
            live = (await s.execute(
                select(func.count(Invitation.id)).where(
                    Invitation.customer_id == ctx["customer_id"],
                    Invitation.used_at.is_(None),
                    Invitation.expires_at > datetime.now(UTC),
                )
            )).scalar_one()
            issued = (await s.execute(
                select(func.count(Invitation.id)).where(
                    Invitation.customer_id == ctx["customer_id"],
                )
            )).scalar_one()
        assert live == 1, f"{live} usable invitations for one customer — replay window"
        # The superseded rows are expired, not deleted: what was issued, and by
        # whom, stays answerable.
        assert issued == 3, f"expected 3 issued rows in history, found {issued}"


# ── attribution ──────────────────────────────────────────────────────────────

class TestAttribution:
    async def test_a_minted_customer_invite_records_who_minted_it(
        self, roles_ctx,
    ) -> None:
        """The residual risk on a bearer credential is that it leaves the shop
        deliberately. No role check can prevent that — the row is genuinely the
        minter's to see — so the control is knowing who issued it. Before Batch 6
        `created_by_user_id` was left NULL on this path."""
        from sqlalchemy import select

        from app.models import Invitation

        client, ctx, Session = roles_ctx
        r = await client.post(
            f"{CUSTOMERS}/{ctx['customer_id']}/invite", headers=ctx["sales"],
        )
        assert r.status_code == 201, r.text

        async with Session() as s:
            minted_by = (await s.execute(
                select(Invitation.created_by_user_id).where(
                    Invitation.customer_id == ctx["customer_id"],
                    Invitation.used_at.is_(None),
                )
            )).scalar_one()
        assert minted_by is not None, "the invitation records no minter"
        assert str(minted_by) == ctx["sales_user_id"]


# ── the UI is not the control ────────────────────────────────────────────────

async def test_the_api_is_the_boundary_not_the_screen(roles_ctx) -> None:
    """Every denial above is a direct API call with a valid session — no screen
    involved. Restated as its own test because "the UI hides it" is the reasoning
    this repository has had to correct more than once."""
    client, ctx, _ = roles_ctx
    r = await client.post(
        INVITATIONS, headers=ctx["sales"], json={"kind": "staff", "role": "owner"},
    )
    assert r.status_code == 403
    assert "token" not in r.text
