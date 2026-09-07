"""Owner/manager-scoped tenant reads must enforce their role server-side.

Seven tenant-scoped GETs return the shop's private configuration, staff roster,
commercial relationship with the platform and customer SMS log. All were
tenant-bound but role-ungated, so a `sales` seller could pull them with a direct
API call; only `GET /billing/topups` was guarded (SEC-009 swept that one route
and not its siblings).

This module is the *matrix* for that whole family: every route lives in
``GUARDED_READS`` and every route is asserted against every role in
``TenantRole``. A sibling cannot silently lose its guard, and a newly added role
fails ``test_every_tenant_role_is_classified`` until it is deliberately placed on
the allowed or denied side.

DB-backed, exercised through the real ``resolve_tenant`` dependency and real
tokens — authorization is never mocked.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

SETTINGS = "/api/tenant/settings"
STAFF = "/api/tenant/staff"
BILLING = "/api/tenant/billing"
INVOICES = "/api/tenant/invoices"
TOPUP_INFO = "/api/tenant/billing/topup-info"
SMS_USAGE = "/api/tenant/sms/usage"
SMS_HISTORY = "/api/tenant/sms/history"
TOPUPS = "/api/tenant/billing/topups"

#: Every tenant read that exposes owner/manager-scoped business data.
#: ``id`` is the finding that put the route here.
GUARDED_READS: list[tuple[str, str]] = [
    (SETTINGS, "QA-004"),
    (STAFF, "QA-005"),
    (BILLING, "QA-006"),
    (INVOICES, "QA-006"),
    (TOPUP_INFO, "QA-006"),
    (SMS_USAGE, "QA-006"),
    (SMS_HISTORY, "QA-006"),
    (TOPUPS, "SEC-009"),  # already guarded — the contrast case, kept so it stays that way
]

#: Roles that may read the routes above, and roles that may not. Together these
#: must cover ``TenantRole`` exactly (see test_every_tenant_role_is_classified).
ALLOWED_ROLES = ("owner", "manager")
DENIED_ROLES = ("sales",)


def _ids(routes: list[tuple[str, str]]) -> list[str]:
    return [f"{fid}:{path}" for path, fid in routes]


@pytest_asyncio.fixture
async def roles_ctx():
    """Two tenants. Tenant A has an owner, a manager and a sales seller; tenant B
    has its own owner, so cross-tenant leakage is observable."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Tenant, TenantUser, User
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
            a = Tenant(slug="tenant-a", name="Tenant A")
            b = Tenant(slug="tenant-b", name="Tenant B")
            s.add_all([a, b])
            await s.flush()
            owner, manager, sales, other_owner = (
                User(telegram_id=210001, telegram_username="ownr"),
                User(telegram_id=210002, telegram_username="mgr"),
                User(telegram_id=210003, telegram_username="clerk"),
                User(telegram_id=210004, telegram_username="ownr_b"),
            )
            s.add_all([owner, manager, sales, other_owner])
            await s.flush()
            s.add_all([
                TenantUser(tenant_id=a.id, user_id=owner.id, role=TenantRole.owner),
                TenantUser(tenant_id=a.id, user_id=manager.id, role=TenantRole.manager),
                TenantUser(tenant_id=a.id, user_id=sales.id, role=TenantRole.sales),
                TenantUser(tenant_id=b.id, user_id=other_owner.id, role=TenantRole.owner),
            ])
            await s.commit()
            ctx = {
                "owner": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
                "manager": {"Authorization": f"Bearer {issue_token(user_id=str(manager.id))}"},
                "sales": {"Authorization": f"Bearer {issue_token(user_id=str(sales.id))}"},
                "other_owner": {"Authorization": f"Bearer {issue_token(user_id=str(other_owner.id))}"},
            }
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, ctx
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# ── the matrix ──────────────────────────────────────────────────────────────

async def test_every_tenant_role_is_classified() -> None:
    """Adding a role to the enum must force a deliberate allow/deny decision here
    rather than silently defaulting to whatever the handlers happen to do."""
    from app.models.enums import TenantRole

    classified = set(ALLOWED_ROLES) | set(DENIED_ROLES)
    actual = {r.value for r in TenantRole}
    assert classified == actual, (
        f"TenantRole changed: {actual ^ classified} is unclassified. "
        "Add it to ALLOWED_ROLES or DENIED_ROLES and extend the guards."
    )


@pytest.mark.parametrize(("path", "finding"), GUARDED_READS, ids=_ids(GUARDED_READS))
@pytest.mark.parametrize("role", DENIED_ROLES)
async def test_lower_roles_are_denied(roles_ctx, path: str, finding: str, role: str) -> None:
    """The defect: a counter clerk could read the shop's private business data."""
    client, ctx = roles_ctx
    r = await client.get(path, headers=ctx[role])
    assert r.status_code == 403, f"[{finding}] {role} must not read {path}, got {r.status_code}"
    assert r.json()["detail"] == "owner_or_manager_only"


@pytest.mark.parametrize(("path", "finding"), GUARDED_READS, ids=_ids(GUARDED_READS))
@pytest.mark.parametrize("role", ALLOWED_ROLES)
async def test_allowed_roles_retain_access(roles_ctx, path: str, finding: str, role: str) -> None:
    """The fix must not narrow the roles that legitimately use these screens."""
    client, ctx = roles_ctx
    r = await client.get(path, headers=ctx[role])
    assert r.status_code == 200, f"[{finding}] {role} lost access to {path}: {r.status_code}"


@pytest.mark.parametrize(("path", "finding"), GUARDED_READS, ids=_ids(GUARDED_READS))
async def test_unauthenticated_is_rejected(roles_ctx, path: str, finding: str) -> None:
    client, _ = roles_ctx
    r = await client.get(path)
    assert r.status_code == 401, f"[{finding}] {path} answered anon with {r.status_code}"


# ── behaviour the guards must not disturb ───────────────────────────────────

async def test_tenant_isolation_preserved(roles_ctx) -> None:
    """Each owner still sees only their own tenant's roster."""
    client, ctx = roles_ctx
    a = await client.get(STAFF, headers=ctx["owner"])
    b = await client.get(STAFF, headers=ctx["other_owner"])
    assert a.status_code == b.status_code == 200
    a_users = {row["telegram_username"] for row in a.json()}
    b_users = {row["telegram_username"] for row in b.json()}
    assert a_users == {"ownr", "mgr", "clerk"}
    assert b_users == {"ownr_b"}
    assert not (a_users & b_users), "staff roster leaked across tenants"


async def test_staff_list_shape_unchanged(roles_ctx) -> None:
    client, ctx = roles_ctx
    r = await client.get(STAFF, headers=ctx["owner"])
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list) and len(rows) == 3
    assert set(rows[0]) == {
        "id", "telegram_id", "telegram_username", "first_name", "last_name",
        "role", "is_active", "last_seen_at", "invitation_status",
    }
    assert {row["role"] for row in rows} == {"owner", "manager", "sales"}


async def test_settings_response_shape_and_secret_masking_unchanged(roles_ctx) -> None:
    """The guard must not alter the payload or unmask provider secrets."""
    client, ctx = roles_ctx
    r = await client.get(SETTINGS, headers=ctx["owner"])
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "bot", "calc", "customer_form", "payments", "product_form",
        "profile", "reminders", "risk", "sms", "updated_at",
    }
    for key in ("click_secret_key", "payme_secret_key"):
        secret = body["payments"][key]
        assert secret is None or secret.startswith("*"), f"{key} came back unmasked"


async def test_settings_patch_behaviour_unchanged(roles_ctx) -> None:
    """PATCH keeps its own guard and still writes: owner/manager 200, sales 403."""
    client, ctx = roles_ctx
    r = await client.patch(SETTINGS, headers=ctx["owner"],
                           json={"profile": {"business_name": "Muddato Shop"}})
    assert r.status_code == 200
    after = await client.get(SETTINGS, headers=ctx["manager"])
    assert after.json()["profile"]["business_name"] == "Muddato Shop"

    denied = await client.patch(SETTINGS, headers=ctx["sales"],
                                json={"profile": {"business_name": "hijack"}})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "owner_or_manager_only"


async def test_topups_contract_and_offset_unchanged(roles_ctx) -> None:
    """SEC-009's route is the contrast case: it was already correct and stays so."""
    client, ctx = roles_ctx
    r = await client.get(TOPUPS, headers=ctx["owner"])
    assert r.status_code == 200
    assert r.json() == []
    r2 = await client.get(f"{TOPUPS}?offset=0", headers=ctx["owner"])
    assert r2.status_code == 200
    assert r2.json() == []


async def test_invoices_offset_contract_unchanged(roles_ctx) -> None:
    client, ctx = roles_ctx
    r = await client.get(f"{INVOICES}?limit=10&offset=0", headers=ctx["owner"])
    assert r.status_code == 200
    assert r.json() == []
