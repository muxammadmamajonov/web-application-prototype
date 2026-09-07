"""MUDD-137 review follow-up — the audit-diff behaviour PR #1047 shipped had
zero test coverage. These pin down the five behaviours the review named:

  1. a commission-rate change (money-affecting) writes a `tenant_settings.update` row
  2. a no-op PATCH (same values resubmitted) writes no row at all
  3. a Payme/Click secret *rotation* (secret A -> secret B) is audited even though
     `bool(v)` stays True on both sides, and no plaintext ever reaches the log
  4. a staff role change writes a `staff.update` row
  5. a `card_payment_instructions` change — the text telling a customer which card
     to transfer money to — is audited via a changed-marker, never verbatim

DB-backed, exercised through the real HTTP routes and a real token — same
harness shape as tests/test_sec009_billing_topups_role_guard.py.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

SETTINGS = "/api/tenant/settings"


@pytest_asyncio.fixture
async def owner_ctx():
    """One tenant with an owner and a plain staff member, plus real tokens."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import Tenant, TenantSettings, TenantUser, User
    from app.models.enums import TenantRole

    engine = create_async_engine(url)
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
            t = Tenant(slug="audit-gaps", name="Audit Gaps Co")
            s.add(t)
            await s.flush()
            owner, staff = User(telegram_id=910001), User(telegram_id=910002)
            s.add_all([owner, staff])
            await s.flush()
            s.add_all([
                TenantUser(tenant_id=t.id, user_id=owner.id, role=TenantRole.owner),
                TenantUser(tenant_id=t.id, user_id=staff.id, role=TenantRole.sales),
            ])
            settings_row = TenantSettings(tenant_id=t.id)
            s.add(settings_row)
            await s.commit()
            ctx = {
                "tenant_id": str(t.id),
                "staff_tu_id": (await s.execute(
                    __import__("sqlalchemy").select(TenantUser.id).where(
                        TenantUser.tenant_id == t.id, TenantUser.user_id == staff.id,
                    )
                )).scalar_one(),
                "owner_headers": {"Authorization": f"Bearer {issue_token(user_id=str(owner.id))}"},
            }
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            yield client, ctx, Session
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _audit_rows(Session, tenant_id: str, action: str) -> list:
    from sqlalchemy import select

    from app.models import AuditLog

    async with Session() as s:
        rows = (await s.execute(
            select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
        )).scalars().all()
        return list(rows)


async def test_commission_rate_change_writes_audit_row(owner_ctx) -> None:
    client, ctx, Session = owner_ctx
    r = await client.patch(
        SETTINGS, headers=ctx["owner_headers"],
        json={"calc": {"commission_rate_pct": "5.00"}},
    )
    assert r.status_code == 200, r.text

    rows = await _audit_rows(Session, ctx["tenant_id"], "tenant_settings.update")
    assert len(rows) == 1
    assert rows[0].before_json["commission_rate_pct"] != rows[0].after_json["commission_rate_pct"]
    assert rows[0].after_json["commission_rate_pct"] == 5.0


async def test_noop_patch_writes_no_audit_row(owner_ctx) -> None:
    client, ctx, Session = owner_ctx
    # Establish a baseline value first.
    r1 = await client.patch(
        SETTINGS, headers=ctx["owner_headers"],
        json={"calc": {"commission_rate_pct": "3.00"}},
    )
    assert r1.status_code == 200, r1.text
    baseline_rows = await _audit_rows(Session, ctx["tenant_id"], "tenant_settings.update")
    assert len(baseline_rows) == 1

    # Resubmit the exact same value — must NOT write a second row.
    r2 = await client.patch(
        SETTINGS, headers=ctx["owner_headers"],
        json={"calc": {"commission_rate_pct": "3.00"}},
    )
    assert r2.status_code == 200, r2.text
    rows_after = await _audit_rows(Session, ctx["tenant_id"], "tenant_settings.update")
    assert len(rows_after) == 1, "a no-op PATCH must not write a second audit row"


async def test_secret_rotation_is_audited_without_leaking_the_value(owner_ctx) -> None:
    client, ctx, Session = owner_ctx

    r1 = await client.patch(
        SETTINGS, headers=ctx["owner_headers"],
        json={"payments": {"payme_secret_key": "first-secret-value"}},
    )
    assert r1.status_code == 200, r1.text
    rows = await _audit_rows(Session, ctx["tenant_id"], "tenant_settings.update")
    assert len(rows) == 1, "unset -> set must be audited"
    assert rows[0].after_json["payme_secret_key"] is True   # presence, never the value

    # Rotate: secret A -> secret B. `bool(v)` stays True on both sides, so
    # without the *_rotated marker this would silently write nothing.
    r2 = await client.patch(
        SETTINGS, headers=ctx["owner_headers"],
        json={"payments": {"payme_secret_key": "second-secret-value"}},
    )
    assert r2.status_code == 200, r2.text
    rows = await _audit_rows(Session, ctx["tenant_id"], "tenant_settings.update")
    assert len(rows) == 2, "a secret rotation (A -> B) must be audited even though presence did not change"

    rotation_row = rows[1]
    assert rotation_row.after_json.get("payme_secret_key_rotated") is True
    assert rotation_row.before_json.get("payme_secret_key_rotated") is False

    # No plaintext, ever, in either direction.
    for row in rows:
        for payload in (row.before_json, row.after_json):
            for v in payload.values():
                if isinstance(v, str):
                    assert v not in ("first-secret-value", "second-secret-value")


async def test_card_payment_instructions_change_is_audited_without_leaking_the_value(owner_ctx) -> None:
    client, ctx, Session = owner_ctx

    r1 = await client.patch(
        SETTINGS, headers=ctx["owner_headers"],
        json={"payments": {"card_payment_instructions": "Card 8600 1234 5678 9012 — Ismoilov A."}},
    )
    assert r1.status_code == 200, r1.text
    rows = await _audit_rows(Session, ctx["tenant_id"], "tenant_settings.update")
    assert len(rows) == 1, "changing the customer-facing payment card instructions must be audited"
    assert rows[0].after_json.get("card_payment_instructions_changed") is True
    assert rows[0].before_json.get("card_payment_instructions_changed") is False

    # Resubmitting the exact same instructions must NOT write a second row.
    r2 = await client.patch(
        SETTINGS, headers=ctx["owner_headers"],
        json={"payments": {"card_payment_instructions": "Card 8600 1234 5678 9012 — Ismoilov A."}},
    )
    assert r2.status_code == 200, r2.text
    rows_after = await _audit_rows(Session, ctx["tenant_id"], "tenant_settings.update")
    assert len(rows_after) == 1, "resubmitting the same card instructions must not write a second row"

    # The card number/name itself must never reach the audit log — only the marker.
    for row in rows_after:
        for payload in (row.before_json, row.after_json):
            for v in payload.values():
                if isinstance(v, str):
                    assert "8600 1234 5678 9012" not in v


async def test_role_change_writes_staff_update_audit_row(owner_ctx) -> None:
    client, ctx, Session = owner_ctx
    r = await client.patch(
        f"/api/tenant/staff/{ctx['staff_tu_id']}", headers=ctx["owner_headers"],
        json={"role": "manager"},
    )
    assert r.status_code == 200, r.text

    rows = await _audit_rows(Session, ctx["tenant_id"], "staff.update")
    assert len(rows) == 1
    assert rows[0].before_json["role"] == "sales"
    assert rows[0].after_json["role"] == "manager"
