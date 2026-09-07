"""QA-R-001 — the invoice **PDF** must enforce the same role as the invoice **JSON**.

The defect
----------
Pass 2 closed ``GET /api/tenant/invoices`` to owner/manager (QA-006), but the
sibling ``GET /api/tenant/invoices/{invoice_id}/invoice.pdf`` was left
role-ungated: it read ``tenant, _ = ctx`` and applied no role check. A ``sales``
clerk reached the renderer and, on a host where WeasyPrint's native libraries
are present — which ``backend/Dockerfile`` provisions in both stages — would
receive the shop's SaaS billing document.

The re-audit could only observe a 500 (`pdf_render_failed`) locally, because
pango/cairo are absent here. That 500 *is* the finding: the request was not
rejected at 403, it passed authorization and reached the renderer.

Why the invoice and not its siblings
------------------------------------
The contract and receipt PDFs (``pdf.py:84``, ``:106``) deliberately use
object-level ``_can_access_installment`` — they are *operational* documents that
a clerk and the customer legitimately need. The invoice is the shop's
**commercial relationship with the platform**, which is why its JSON list is
owner/manager-only. This module asserts that asymmetry stays deliberate.

Scope of mocking
----------------
Authorization is **never** mocked: real tokens, the real ``resolve_tenant``
dependency, real ``TenantUser`` rows, the real migrated-model schema. Only
:func:`app.services.pdf.render_invoice_pdf` is monkeypatched, to deterministic
bytes — otherwise every 200-case assertion would be blocked by the missing
WeasyPrint system libraries rather than by anything about roles.

This module is an **exhaustive route-level matrix over every ``TenantRole``**:
:func:`test_every_tenant_role_is_classified` fails until a newly added role is
deliberately placed on the allowed or denied side, so this route cannot quietly
regain the gap it just lost.
"""
from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

#: Deterministic stand-in for a rendered invoice — a real PDF magic header, so
#: the response is indistinguishable from a genuine render at the HTTP layer.
FAKE_PDF = b"%PDF-1.7\n%\xc2\xb5muddato-qar001\ntrailer\n%%EOF\n"

ALLOWED_ROLES = ("owner", "manager")
DENIED_ROLES = ("sales",)


def _pdf_path(invoice_id) -> str:
    return f"/api/tenant/invoices/{invoice_id}/invoice.pdf"


@pytest_asyncio.fixture
async def invoice_ctx(monkeypatch):
    """Tenant A (owner + manager + sales) with one Invoice; tenant B with its own
    owner, so cross-tenant non-disclosure is observable on the same route."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Invoice, Tenant, TenantUser, User
    from app.models.enums import TenantRole

    # Once TEST_DATABASE_URL is set the DB tier is mandatory (QA-R-002): a refused
    # connection, a privilege error or an invalid schema must FAIL this run.
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with Session() as session:
            yield session

    # Only the renderer is stubbed — never authorization. Counted so a denied
    # role can be proven never to have reached it.
    calls: list = []

    async def _fake_render(db, invoice_id):
        calls.append(invoice_id)
        return FAKE_PDF

    monkeypatch.setattr("app.services.pdf.render_invoice_pdf", _fake_render)
    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with Session() as s:
            a = Tenant(slug="tenant-a", name="Tenant A")
            b = Tenant(slug="tenant-b", name="Tenant B")
            s.add_all([a, b])
            await s.flush()
            owner, manager, sales, other_owner = (
                User(telegram_id=310001, telegram_username="ownr"),
                User(telegram_id=310002, telegram_username="mgr"),
                User(telegram_id=310003, telegram_username="clerk"),
                User(telegram_id=310004, telegram_username="ownr_b"),
            )
            s.add_all([owner, manager, sales, other_owner])
            await s.flush()
            now = datetime.now(UTC)
            inv = Invoice(
                tenant_id=a.id,
                plan_code="lite",
                invoice_number="2026-000001",
                period_start=date(2026, 7, 1),
                period_end=date(2026, 7, 31),
                amount=Decimal("120000.00"),
                issued_at=now,
                due_at=now,
            )
            s.add_all([
                TenantUser(tenant_id=a.id, user_id=owner.id, role=TenantRole.owner),
                TenantUser(tenant_id=a.id, user_id=manager.id, role=TenantRole.manager),
                TenantUser(tenant_id=a.id, user_id=sales.id, role=TenantRole.sales),
                TenantUser(tenant_id=b.id, user_id=other_owner.id, role=TenantRole.owner),
                inv,
            ])
            await s.commit()
            ctx = {
                "invoice_id": inv.id,
                "calls": calls,
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


# ── the matrix, exhaustive over TenantRole ──────────────────────────────────

async def test_every_tenant_role_is_classified() -> None:
    """A new role must force a deliberate allow/deny decision for this route."""
    from app.models.enums import TenantRole

    classified = set(ALLOWED_ROLES) | set(DENIED_ROLES)
    actual = {r.value for r in TenantRole}
    assert classified == actual, (
        f"TenantRole changed: {actual ^ classified} is unclassified. "
        "Add it to ALLOWED_ROLES or DENIED_ROLES and extend the guard."
    )


@pytest.mark.parametrize("role", DENIED_ROLES)
async def test_denied_roles_get_403(invoice_ctx, role: str) -> None:
    """The defect: a counter clerk could pull the shop's SaaS billing PDF."""
    client, ctx = invoice_ctx
    r = await client.get(_pdf_path(ctx["invoice_id"]), headers=ctx[role])
    assert r.status_code == 403, f"{role} must not read the invoice PDF, got {r.status_code}"
    assert r.json()["detail"] == "owner_or_manager_only"


@pytest.mark.parametrize("role", DENIED_ROLES)
async def test_denied_role_is_rejected_before_lookup_or_render(invoice_ctx, role: str) -> None:
    """The guard must run *before* the invoice lookup and the renderer.

    Asking for an invoice id that does not exist is the discriminator: if the
    guard ran after the lookup the clerk would get 404, and the route would be
    leaking existence information to a principal with no business reading it.
    """
    client, ctx = invoice_ctx
    r = await client.get(_pdf_path(uuid4()), headers=ctx[role])
    assert r.status_code == 403, (
        f"{role} got {r.status_code} for a non-existent invoice — the role guard "
        "runs after the lookup instead of before it"
    )
    assert ctx["calls"] == [], "renderer was reached by a denied role"


@pytest.mark.parametrize("role", ALLOWED_ROLES)
async def test_allowed_roles_get_the_pdf(invoice_ctx, role: str) -> None:
    """The fix must not narrow the roles that legitimately download invoices."""
    client, ctx = invoice_ctx
    invoice_id = ctx["invoice_id"]
    r = await client.get(_pdf_path(invoice_id), headers=ctx[role])
    assert r.status_code == 200, f"{role} lost invoice PDF access: {r.status_code}"
    assert r.content == FAKE_PDF
    assert ctx["calls"] == [invoice_id]


@pytest.mark.parametrize("role", ALLOWED_ROLES)
async def test_response_contract_unchanged(invoice_ctx, role: str) -> None:
    """Content type, disposition and cache headers are part of the contract."""
    client, ctx = invoice_ctx
    invoice_id = ctx["invoice_id"]
    r = await client.get(_pdf_path(invoice_id), headers=ctx[role])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'inline; filename="invoice-{invoice_id}.pdf"'
    assert r.headers["cache-control"] == "private, no-store"


async def test_anonymous_is_rejected(invoice_ctx) -> None:
    client, ctx = invoice_ctx
    r = await client.get(_pdf_path(ctx["invoice_id"]))
    assert r.status_code == 401, f"anon got {r.status_code}"
    assert ctx["calls"] == []


async def test_other_tenant_owner_gets_404_not_403(invoice_ctx) -> None:
    """Tenant non-disclosure: an outsider must not learn the invoice exists.

    Tenant B's owner *passes* the role guard — they really are an owner — so
    this also proves the new guard did not replace the tenant scoping.
    """
    client, ctx = invoice_ctx
    r = await client.get(_pdf_path(ctx["invoice_id"]), headers=ctx["other_owner"])
    assert r.status_code == 404, f"cross-tenant read returned {r.status_code}"
    assert r.json()["detail"] == "invoice_not_found"
    assert ctx["calls"] == []


async def test_missing_invoice_is_404_for_an_allowed_role(invoice_ctx) -> None:
    """An owner asking for a genuinely absent invoice still gets 404, not 403."""
    client, ctx = invoice_ctx
    r = await client.get(_pdf_path(uuid4()), headers=ctx["owner"])
    assert r.status_code == 404
    assert r.json()["detail"] == "invoice_not_found"


async def test_sibling_operational_pdfs_keep_object_level_access(invoice_ctx) -> None:
    """Contract and receipt PDFs must **not** inherit the owner/manager rule.

    They are operational documents intentionally available to tenant members
    (and the customer). A clerk hitting them must fail on the *object* check
    (404 — no such installment/payment), never on a role check (403).
    """
    client, ctx = invoice_ctx
    for path in (
        f"/api/tenant/installments/{uuid4()}/contract.pdf",
        f"/api/tenant/payments/{uuid4()}/receipt.pdf",
    ):
        r = await client.get(path, headers=ctx["sales"])
        assert r.status_code == 404, f"{path} answered sales with {r.status_code}"
        assert r.json()["detail"] != "owner_or_manager_only"
