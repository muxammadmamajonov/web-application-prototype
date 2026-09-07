"""MUDD-54 R1 regression: a locked shop must still be able to pay its way back.

MUDD-25 H1 closed a real hole — a ``blocked``/``suspended`` tenant could mutate
its own data — by refusing every mutating method inside ``resolve_tenant``
(``app/api/tenant/_shared.py``). The refusal was total, and the only two
self-service routes OUT of ``suspended``/``blocked`` are themselves POSTs:

    POST /api/tenant/billing/topup   — pay money in
    POST /api/tenant/plan-change     — choose a paid plan

so the guard sealed the exit alongside the entrance. Every trial that expired
and every shop that fell behind on an invoice became unrecoverable without a
platform admin editing the row by hand.

These tests pin BOTH halves of the corrected rule, because either one alone is
satisfiable by a wrong implementation:

  * the recovery routes work while locked  (RED before the fix — 403), and
  * every OTHER mutating route still 403s  (RED if the fix is too wide),

plus the two end-to-end recovery chains that motivate the carve-out at all.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import _pg_test_url

LOCKED_STATUSES = ("blocked", "suspended")


async def _exec(sql: str, params: dict | None = None) -> None:
    engine = create_async_engine(_pg_test_url())
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    finally:
        await engine.dispose()


async def _set_tenant_status(tenant_id: str, status_value: str) -> None:
    await _exec(
        "UPDATE tenants SET status = :status WHERE id = :id",
        {"status": status_value, "id": tenant_id},
    )


async def _tenant_status(tenant_id: str) -> str:
    engine = create_async_engine(_pg_test_url())
    try:
        async with engine.begin() as conn:
            row = await conn.execute(
                text("SELECT status FROM tenants WHERE id = :id"), {"id": tenant_id}
            )
            return str(row.scalar_one())
    finally:
        await engine.dispose()


async def _session():
    """ORM session on the fixture's database.

    Seeds go through the models, not raw SQL: half of ``plans``' NOT NULL columns
    carry Python-side defaults only, so a hand-written INSERT trips on ``features``
    and friends.
    """
    engine = create_async_engine(_pg_test_url())
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _enable_payme() -> None:
    """Configure the Payme provider so ``POST /billing/topup`` can reach a 201.

    Without it the handler still runs but returns 400 ``provider_not_configured``,
    which would not prove the top-up actually succeeded — only that it was not
    refused by the status guard.
    """
    from app.models import PlatformSetting

    engine, Session = await _session()
    try:
        async with Session() as s:
            existing = (await s.execute(
                select(PlatformSetting).where(PlatformSetting.key == "billing_providers")
            )).scalar_one_or_none()
            value = {"payme": {"enabled": True, "merchant_id": "m-test"}}
            if existing is None:
                s.add(PlatformSetting(key="billing_providers", value=value))
            else:
                existing.value = value
            await s.commit()
    finally:
        await engine.dispose()


async def _seed_paid_plan(code: str = "lite", price: int = 0) -> None:
    """A self-selectable plan. Price 0 so plan-change needs no balance — the
    subject under test is the status guard, not the 402 insufficient-balance path."""
    from app.models import Plan

    engine, Session = await _session()
    try:
        async with Session() as s:
            existing = (await s.execute(
                select(Plan).where(Plan.code == code)
            )).scalar_one_or_none()
            if existing is None:
                s.add(Plan(
                    code=code, name_uz="Lite", name_ru="Lite",
                    price_monthly=Decimal(price), is_active=True, is_contact_sales=False,
                ))
            else:
                existing.is_active = True
                existing.price_monthly = Decimal(price)
            await s.commit()
    finally:
        await engine.dispose()


async def _install_invoice_sequence_fn() -> None:
    """Install ``next_invoice_sequence`` into the model-only fixture schema.

    ``isolation_ctx`` builds tables from the SQLAlchemy models, so nothing a
    migration creates by raw SQL exists here — and ``plan-change`` issues an
    invoice, which calls this function (``services/billing.py`` ->
    ``next_invoice_number``). Body mirrors migration ``r99_global_rls_least_privilege``;
    the SECURITY DEFINER / GRANT wrapper is a privilege concern of the migrated
    schema and has no meaning in this single-role fixture.
    """
    await _exec(
        """
        CREATE OR REPLACE FUNCTION public.next_invoice_sequence(p_year integer)
        RETURNS bigint
        LANGUAGE plpgsql
        AS $$
        DECLARE next_value bigint;
        BEGIN
          INSERT INTO public.sequence_counters (name, value)
          VALUES ('invoice:' || p_year::text, 1)
          ON CONFLICT (name) DO UPDATE
            SET value = public.sequence_counters.value + 1
          RETURNING value INTO next_value;
          RETURN next_value;
        END;
        $$
        """
    )


async def _seed_overdue_invoice(tenant_id: str, amount: int = 500_000) -> None:
    """One invoice past its due date — the state ``run_dunning`` suspends on."""
    from app.models import Invoice
    from app.models.enums import InvoiceStatus

    engine, Session = await _session()
    try:
        async with Session() as s:
            s.add(Invoice(
                tenant_id=UUID(tenant_id), plan_code="lite",
                invoice_number="MUDD-54-INV",
                period_start=date(2026, 7, 1), period_end=date(2026, 7, 31),
                amount=Decimal(amount), status=InvoiceStatus.overdue,
                issued_at=datetime.now(UTC) - timedelta(days=50),
                due_at=datetime.now(UTC) - timedelta(days=20),
            ))
            await s.commit()
    finally:
        await engine.dispose()


# ── the carve-out: locked tenants may still pay ─────────────────────────────

@pytest.mark.parametrize("locked_status", LOCKED_STATUSES)
async def test_locked_tenant_can_create_topup(isolation_ctx, locked_status: str) -> None:
    """RED before the fix: 403 tenant_blocked_or_suspended."""
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    await _enable_payme()
    await _set_tenant_status(ctx["tenant_a"], locked_status)

    resp = await client.post(
        "/api/tenant/billing/topup", headers=auth,
        data={"amount": "500000", "method": "payme"},
    )

    assert resp.status_code == 201, (
        f"a {locked_status} tenant must be able to top up — got "
        f"{resp.status_code}: {resp.text[:300]}"
    )
    assert resp.json().get("checkout_url"), "expected a hosted-checkout URL to pay at"


@pytest.mark.parametrize("locked_status", LOCKED_STATUSES)
async def test_locked_tenant_can_change_plan(isolation_ctx, locked_status: str) -> None:
    """RED before the fix: 403 tenant_blocked_or_suspended."""
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    await _seed_paid_plan()
    await _install_invoice_sequence_fn()
    await _set_tenant_status(ctx["tenant_a"], locked_status)

    resp = await client.post(
        "/api/tenant/plan-change", headers=auth, json={"plan_code": "lite"},
    )

    assert resp.status_code == 200, (
        f"a {locked_status} tenant must be able to select a paid plan — got "
        f"{resp.status_code}: {resp.text[:300]}"
    )


# ── the carve-out must stay narrow: H1 is otherwise intact ──────────────────

@pytest.mark.parametrize("locked_status", LOCKED_STATUSES)
async def test_locked_tenant_still_denied_on_operational_writes(
    isolation_ctx, locked_status: str,
) -> None:
    """MUDD-25 H1 must survive the carve-out: ordinary business writes still 403."""
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    await _set_tenant_status(ctx["tenant_a"], locked_status)

    resp = await client.post(
        "/api/tenant/customers", headers=auth,
        json={"full_name": "Should Not Be Created", "phone": "+998901234567"},
    )
    assert resp.status_code == 403, f"got {resp.status_code}: {resp.text[:300]}"
    assert resp.json().get("detail") == "tenant_blocked_or_suspended"


async def test_carve_out_is_not_a_path_prefix() -> None:
    """The allowlist must name whole routes, never ``/billing/*``.

    Opening the prefix would hand a locked tenant every present and future route
    under it. Pure unit test — no database, so it runs in every tier.
    """
    from app.services.tenant_status import RECOVERY_ROUTES, is_recovery_route

    assert is_recovery_route("POST", "/api/tenant/billing/topup")
    assert is_recovery_route("POST", "/api/tenant/plan-change")

    # Neighbours, extensions and method swaps are all outside the carve-out.
    for method, path in [
        ("POST", "/api/tenant/billing"),
        ("POST", "/api/tenant/billing/topup/approve"),
        ("POST", "/api/tenant/billing/topups"),
        ("DELETE", "/api/tenant/billing/topup"),
        ("POST", "/api/tenant/plan-change/confirm"),
        ("POST", "/api/tenant/customers"),
    ]:
        assert not is_recovery_route(method, path), f"{method} {path} must not be allowlisted"

    # Whatever the set holds, every entry is a full absolute path under /api/.
    for method, path in RECOVERY_ROUTES:
        assert method == "POST", f"only pay-in POSTs belong here, found {method}"
        assert path.startswith("/api/tenant/"), path
        assert "*" not in path and not path.endswith("/"), path


# ── end-to-end: the two ways a locked shop gets back to active ──────────────

async def test_recovery_chain_unpaid_invoice_topup_credit_reactivates(isolation_ctx) -> None:
    """Non-payment lock: suspended → topup → provider credit → settled → active.

    This is the chain ``run_dunning`` creates. The credit step calls the very
    service the Payme/Click webhook calls (``mark_topup_paid``), so the
    reactivation in ``settle_invoices_from_balance`` is exercised for real.
    """
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    await _enable_payme()

    # A shop suspended by dunning for one overdue invoice.
    await _seed_overdue_invoice(ctx["tenant_a"])
    await _set_tenant_status(ctx["tenant_a"], "suspended")

    # 1) the owner pays — this is the request the guard used to refuse
    resp = await client.post(
        "/api/tenant/billing/topup", headers=auth,
        data={"amount": "500000", "method": "payme"},
    )
    assert resp.status_code == 201, f"{resp.status_code}: {resp.text[:300]}"
    topup_id = resp.json()["id"]

    # 2) the provider confirms — same entry point the webhook uses
    from app.models import TenantTopup
    from app.services.subscription_checkout import mark_topup_paid

    engine = create_async_engine(_pg_test_url())
    try:
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as s:
            topup = await s.get(TenantTopup, UUID(topup_id))
            assert topup is not None
            credited = await mark_topup_paid(
                s, topup, provider_ref="payme-test", captured_amount=Decimal("500000"),
            )
            await s.commit()
        assert credited is True
    finally:
        await engine.dispose()

    # 3) the invoice is settled and the shop is trading again
    assert await _tenant_status(ctx["tenant_a"]) == "active"

    # …and the writes H1 was protecting are open again.
    resp = await client.post(
        "/api/tenant/customers", headers=auth,
        json={"full_name": "Back In Business", "phone": "+998901234599"},
    )
    assert resp.status_code == 201, f"{resp.status_code}: {resp.text[:300]}"


@pytest.mark.parametrize("locked_status", LOCKED_STATUSES)
async def test_recovery_chain_expired_trial_plan_change_reactivates(
    isolation_ctx, locked_status: str,
) -> None:
    """Trial-expiry lock: there are no invoices, so settlement can never reactivate.

    ``enforce_trials_job`` drives ``demo → suspended → blocked`` with nothing owed,
    which leaves ``plan-change`` as the ONLY exit. It must lift both locks —
    lifting only ``suspended`` strands every shop the ``on_expiry='block'``
    setting sent straight to ``blocked``.
    """
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    await _seed_paid_plan()
    await _install_invoice_sequence_fn()
    await _set_tenant_status(ctx["tenant_a"], locked_status)

    resp = await client.post(
        "/api/tenant/plan-change", headers=auth, json={"plan_code": "lite"},
    )
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:300]}"
    assert await _tenant_status(ctx["tenant_a"]) == "active"

    resp = await client.post(
        "/api/tenant/customers", headers=auth,
        json={"full_name": "Paid Up", "phone": "+998901234598"},
    )
    assert resp.status_code == 201, f"{resp.status_code}: {resp.text[:300]}"


# ── R4: the super-admin bypass is deliberate, and audited ───────────────────

async def test_super_admin_may_mutate_locked_tenant_and_is_audited(isolation_ctx) -> None:
    """The impersonation branch returns ABOVE the locked-tenant guard.

    That is intentional — an operator must be able to act on a shop the lock
    itself is what's wrong with — but it is exactly the kind of ordering that
    turns into an unexamined hole once nobody remembers it was a decision. Pin
    both halves: the bypass works, AND every mutating request through it leaves
    a ``tenant.impersonated_mutation`` row. If someone later moves the return
    below the guard, or drops the audit write, this test says so.
    """
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx

    from app.core.security import issue_token
    from app.models import AuditLog, User
    from app.models.enums import PlatformRole

    engine, Session = await _session()
    try:
        async with Session() as s:
            admin = User(telegram_id=100999, platform_role=PlatformRole.super_admin)
            s.add(admin)
            await s.commit()
            admin_id = str(admin.id)
    finally:
        await engine.dispose()

    await _set_tenant_status(ctx["tenant_a"], "blocked")

    auth = {
        "Authorization": f"Bearer {issue_token(user_id=admin_id)}",
        "X-Tenant-Id": ctx["tenant_a"],
    }
    resp = await client.post(
        "/api/tenant/customers", headers=auth,
        json={"full_name": "Support Fixup", "phone": "+998901234577"},
    )

    assert resp.status_code == 201, (
        "a super-admin must still be able to act on a locked shop — got "
        f"{resp.status_code}: {resp.text[:300]}"
    )

    engine, Session = await _session()
    try:
        async with Session() as s:
            rows = (await s.execute(
                select(AuditLog).where(AuditLog.action == "tenant.impersonated_mutation")
            )).scalars().all()
    finally:
        await engine.dispose()

    assert rows, "the bypass must leave an impersonated_mutation audit trail"
    assert any(str(r.actor_user_id) == admin_id for r in rows)


# ── the `blocked` lift is conditional, not blanket ──────────────────────────
#
# `blocked` has no reason column: a dunning block, a trial-expiry block and a
# platform-admin block are the same value. The two rival fixes for MUDD-54 each
# picked a constant answer — "never lift" stranded every expired trial (there is
# no invoice for settle_invoices_from_balance to settle), "always lift" let a
# shop shed a debt block or undo an operator's decision by buying a plan. These
# pin the three cases apart. See `services/tenant_status.may_self_clear_block`.

async def _seed_status_change_audit(tenant_id: str, to_status: str) -> None:
    """The row `PATCH /api/platform/tenants/{id}/status` writes — the only
    writer of this action. `run_dunning`/`enforce_trials_job` write none, which
    is exactly what makes its presence a reliable operator-block marker."""
    from app.models import AuditLog

    engine, Session = await _session()
    try:
        async with Session() as s:
            s.add(AuditLog(
                tenant_id=UUID(tenant_id), action="tenant.status_change",
                entity_type="tenant", entity_id=tenant_id,
                before_json={"status": "active"}, after_json={"status": to_status},
            ))
            await s.commit()
    finally:
        await engine.dispose()


async def test_blocked_tenant_with_overdue_invoice_cannot_shed_debt_via_plan_change(
    isolation_ctx,
) -> None:
    """A dunning block stays until the money arrives.

    RED against the "always lift `blocked`" fix: there, buying a 0-price plan
    reactivated a shop whose overdue invoice was still open — the debt survived
    but the lock that was collecting on it did not. The correct exit is topping
    up, which `settle_invoices_from_balance` turns into reactivation only once
    every invoice is actually covered.
    """
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    await _seed_paid_plan()
    await _install_invoice_sequence_fn()
    await _seed_overdue_invoice(ctx["tenant_a"])
    await _set_tenant_status(ctx["tenant_a"], "blocked")

    resp = await client.post(
        "/api/tenant/plan-change", headers=auth, json={"plan_code": "lite"},
    )

    # The route itself stays reachable — that is the MUDD-54 carve-out, and the
    # shop does get the plan it selected. What it must NOT get is the unlock.
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:300]}"
    assert await _tenant_status(ctx["tenant_a"]) == "blocked", (
        "a shop with an open overdue invoice cleared its debt block by buying a plan"
    )
    assert resp.json()["status"] == "blocked"

    # And H1 is still enforced against it afterwards.
    resp = await client.post(
        "/api/tenant/customers", headers=auth,
        json={"full_name": "Still Locked", "phone": "+998901234571"},
    )
    assert resp.status_code == 403


async def test_operator_blocked_tenant_cannot_self_clear_via_plan_change(
    isolation_ctx,
) -> None:
    """A block a platform admin set by hand is not the subject's to undo.

    `PATCH /api/platform/tenants/{id}/status` accepts `blocked`, so the status is
    not purely economic — it is also how an operator acts on fraud or a legal
    hold. RED against the "always lift" fix, which let that shop reactivate
    itself with a 0-price plan while owing nothing.
    """
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    await _seed_paid_plan()
    await _install_invoice_sequence_fn()
    await _set_tenant_status(ctx["tenant_a"], "blocked")
    await _seed_status_change_audit(ctx["tenant_a"], "blocked")

    resp = await client.post(
        "/api/tenant/plan-change", headers=auth, json={"plan_code": "lite"},
    )

    assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:300]}"
    assert await _tenant_status(ctx["tenant_a"]) == "blocked", (
        "a shop an operator blocked by hand undid that decision by buying a plan"
    )


async def test_economic_block_after_an_operator_unblock_is_self_clearable(
    isolation_ctx,
) -> None:
    """The marker is the NEWEST status_change, not the existence of one.

    An admin who blocks and later unblocks leaves two rows; the newest says
    `active`, so a block the scheduler applies afterwards is economic again and
    the ordinary trial-expiry recovery must keep working. Without the ordering
    this degrades into "any tenant an admin ever touched is permanently stuck".
    """
    if not _pg_test_url():
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}
    await _seed_paid_plan()
    await _install_invoice_sequence_fn()
    await _seed_status_change_audit(ctx["tenant_a"], "blocked")
    await _seed_status_change_audit(ctx["tenant_a"], "active")
    await _set_tenant_status(ctx["tenant_a"], "blocked")

    resp = await client.post(
        "/api/tenant/plan-change", headers=auth, json={"plan_code": "lite"},
    )

    assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:300]}"
    assert await _tenant_status(ctx["tenant_a"]) == "active"
