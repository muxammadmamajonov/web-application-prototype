"""MUDD-66 — H7 tenant financial archive + the backend MEDIUM/LOW batch.

Every test here is a MUTATION PROOF: it fails on the code as it stood before this
change and passes after. Where the fix is a *removal* (a hand-rolled clamp, a dead
helper) the assertion is on the surviving contract, not on the absence of a string.

Tiers
-----
* ``migrated_db``  — real Postgres at ``alembic upgrade head`` (H7, M-02, M-03, M-04, M-06)
* ``isolation_ctx``— model-built schema + HTTP client (LOW-2, LOW-5 behaviour)
* no fixture       — pure unit (LOW-1, LOW-3, LOW-4, LOW-6, LOW-7)
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    AuditLog,
    Customer,
    Installment,
    Payment,
    Plan,
    Product,
    Tenant,
    TenantBalanceTxn,
    TenantSubscription,
    TenantUser,
    User,
)
from app.models.enums import (
    InstallmentStatus,
    PaymentMethod,
    PaymentStatus,
    PlatformRole,
    SubscriptionStatus,
    TenantRole,
)

# ─────────────────────────────────────────────────────────────────────────────
# H7 — deleting a tenant must not delete its financial history
# ─────────────────────────────────────────────────────────────────────────────


async def _seed_shop_with_money(engine) -> tuple[uuid.UUID, uuid.UUID]:
    """A tenant with a real book: 2 contracts, 3 payments, staff, prepaid balance.

    Returns ``(tenant_id, super_admin_user_id)``.
    """
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="h7-shop", name="H7 Shop", balance=Decimal("250000.00"))
        s.add(t)
        await s.flush()

        admin = User(telegram_id=987654, platform_role=PlatformRole.super_admin)
        staff_user = User(telegram_id=987655)
        s.add_all([admin, staff_user])
        await s.flush()
        s.add(TenantUser(tenant_id=t.id, user_id=staff_user.id, role=TenantRole.owner))

        c = Customer(tenant_id=t.id, full_name="H7 Payer", phone="+998900007700")
        s.add(c)
        await s.flush()

        # Contract 1 — active, half paid.
        i1 = Installment(
            tenant_id=t.id, customer_id=c.id, currency="UZS",
            status=InstallmentStatus.active,
            total_amount=Decimal("1000000.00"), principal=Decimal("1200000.00"),
            down_payment=Decimal("0.00"), monthly_amount=Decimal("100000.00"),
            paid_amount=Decimal("300000.00"), months=12,
            start_date=date(2026, 1, 1), next_due_date=date(2026, 2, 1),
        )
        # Contract 2 — completed, paid in full.
        i2 = Installment(
            tenant_id=t.id, customer_id=c.id, currency="UZS",
            status=InstallmentStatus.completed,
            total_amount=Decimal("500000.00"), principal=Decimal("500000.00"),
            down_payment=Decimal("0.00"), monthly_amount=Decimal("500000.00"),
            paid_amount=Decimal("500000.00"), months=1,
            start_date=date(2025, 6, 1), next_due_date=date(2025, 7, 1),
        )
        s.add_all([i1, i2])
        await s.flush()

        now = datetime.now(UTC)
        s.add_all([
            Payment(tenant_id=t.id, installment_id=i1.id, customer_id=c.id,
                    currency="UZS", amount=Decimal("300000.00"),
                    method=PaymentMethod.cash, status=PaymentStatus.approved,
                    paid_at=now - timedelta(days=30)),
            Payment(tenant_id=t.id, installment_id=i2.id, customer_id=c.id,
                    currency="UZS", amount=Decimal("500000.00"),
                    method=PaymentMethod.cash, status=PaymentStatus.approved,
                    paid_at=now - timedelta(days=10)),
            # Money in flight at the moment of deletion — the dispute case.
            Payment(tenant_id=t.id, installment_id=i1.id, customer_id=c.id,
                    currency="UZS", amount=Decimal("100000.50"),
                    method=PaymentMethod.card, status=PaymentStatus.pending,
                    paid_at=now),
            TenantBalanceTxn(tenant_id=t.id, amount=Decimal("250000.00"),
                             kind="topup", balance_after=Decimal("250000.00")),
        ])
        await s.commit()
        return t.id, admin.id


async def test_h7_tenant_delete_archives_the_financial_position(migrated_db) -> None:
    """The `tenant.delete` audit row must carry the money, not just the name.

    RED before the fix: `before` was `{slug, name, status}` — identification only.
    Every figure below was destroyed by the cascade with nothing written down.
    """
    from app.api.platform.tenants import delete_tenant

    tid, admin_id = await _seed_shop_with_money(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    async with Session() as s:
        admin = (await s.execute(select(User).where(User.id == admin_id))).scalar_one()
        await delete_tenant(str(tid), admin, s)

    async with Session() as s:
        assert (await s.execute(
            select(Tenant).where(Tenant.id == tid)
        )).scalar_one_or_none() is None, "the tenant itself must still be deleted"

        row = (await s.execute(
            select(AuditLog).where(
                AuditLog.action == "tenant.delete", AuditLog.entity_id == str(tid),
            )
        )).scalar_one()

    # The audit row outlives the tenant — this is what makes the archive durable
    # (audit_log.tenant_id is ON DELETE SET NULL, not CASCADE).
    assert row.tenant_id is None

    fin = row.before_json["financial"]

    # Accounting position.
    assert fin["principal_total"] == "1700000.00"
    assert fin["paid_total"] == "800000.00"
    assert fin["outstanding_total"] == "900000.00"
    assert Decimal(fin["principal_total"]) - Decimal(fin["paid_total"]) == Decimal(
        fin["outstanding_total"]
    ), "the three figures must cross-check, not just exist"

    assert fin["contracts"]["total"] == 2
    assert fin["contracts"]["by_status"]["active"]["count"] == 1
    assert fin["contracts"]["by_status"]["completed"]["count"] == 1
    assert fin["customers"] == 1
    assert fin["staff"] == 1
    assert fin["currencies"] == ["UZS"]

    # Dispute position: money in flight is broken out, and to the tiyin.
    #
    # (MUDD-114) The scalar `amount` is unchanged and still pinned to the tiyin —
    # this blob is a legal record and its existing readers must keep working. It
    # is now explicitly the `archive_currency` share rather than a sum across
    # every currency the shop ever traded in, and `amount_by_currency` carries
    # the rest. Asserted key-by-key rather than by whole-dict equality so that
    # adding a field is possible without weakening the literals above it.
    approved = fin["payments"]["by_status"]["approved"]
    assert approved["count"] == 2
    assert approved["amount"] == "800000.00"
    assert approved["amount_by_currency"] == {"UZS": "800000.00"}
    pending = fin["payments"]["by_status"]["pending"]
    assert pending["count"] == 1
    assert pending["amount"] == "100000.50", (
        "a truncating int() would have reported 100000 and lost the dispute"
    )
    assert pending["amount_by_currency"] == {"UZS": "100000.50"}

    # The archive names the unit its scalars are in — a bare number is not an
    # accounting record, which is what these three were before MUDD-114.
    assert fin["archive_currency"] == "UZS"
    assert fin["principal_total_by_currency"] == {"UZS": "1700000.00"}
    assert fin["paid_total_by_currency"] == {"UZS": "800000.00"}
    assert fin["outstanding_total_by_currency"] == {"UZS": "900000.00"}
    assert fin["contracts"]["by_status"]["active"]["principal_by_currency"] == {
        "UZS": fin["contracts"]["by_status"]["active"]["principal"]
    }

    # Platform↔tenant side.
    assert fin["balance"] == "250000.00"
    assert fin["balance_txns"] == 1

    # The archive is an aggregate: no personal data survives the deletion.
    blob = str(row.before_json)
    assert "H7 Payer" not in blob and "+998900007700" not in blob

    # Period covered.
    assert fin["first_contract_at"] and fin["last_payment_at"]


def test_h7_the_delete_route_still_points_at_delete_tenant() -> None:
    """The archive helpers must sit ABOVE `@router.delete`, not between it and the
    handler — inserted in between, the decorator registers the HELPER as the route
    and every direct-call test still passes while the endpoint is broken."""
    from app.api.platform import tenants as mod

    delete_routes = [
        r for r in mod.router.routes
        if getattr(r, "path", None) == "/tenants/{tenant_id}" and "DELETE" in getattr(r, "methods", ())
    ]
    assert len(delete_routes) == 1
    assert delete_routes[0].endpoint is mod.delete_tenant


async def test_h7_archive_carries_no_customer_rows_for_an_empty_shop(migrated_db) -> None:
    """A shop with no book still archives a well-formed, zeroed position."""
    from app.api.platform.tenants import delete_tenant

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="h7-empty", name="H7 Empty")
        admin = User(telegram_id=987656, platform_role=PlatformRole.super_admin)
        s.add_all([t, admin])
        await s.commit()
        tid, admin_id = t.id, admin.id

    async with Session() as s:
        admin = (await s.execute(select(User).where(User.id == admin_id))).scalar_one()
        await delete_tenant(str(tid), admin, s)

    async with Session() as s:
        row = (await s.execute(
            select(AuditLog).where(AuditLog.entity_id == str(tid))
        )).scalar_one()
    fin = row.before_json["financial"]
    assert fin["principal_total"] == "0.00"
    assert fin["outstanding_total"] == "0.00"
    assert fin["contracts"] == {"total": 0, "by_status": {}}
    assert fin["first_contract_at"] is None


# ─────────────────────────────────────────────────────────────────────────────
# M-02 — products.cost_price must be Numeric(14,2), like every other money column
# ─────────────────────────────────────────────────────────────────────────────


def test_m02_cost_price_model_declares_precision_and_scale() -> None:
    """RED before: a bare ``Mapped[Decimal | None]`` mapped to unconstrained NUMERIC."""
    from app.models import Product

    col = Product.__table__.c.cost_price
    assert (col.type.precision, col.type.scale) == (14, 2)


async def test_m02_cost_price_column_is_numeric_14_2_in_the_real_schema(migrated_db) -> None:
    """The MIGRATION, not just the model, has to have moved the live column."""
    async with migrated_db.begin() as conn:
        precision, scale = (await conn.execute(text(
            "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
            "WHERE table_name = 'products' AND column_name = 'cost_price'"
        ))).one()
    assert (precision, scale) == (14, 2)


async def test_m02_cost_price_coerces_to_the_scale_it_declares(migrated_db) -> None:
    """A value with 4 decimals no longer round-trips as itself by accident.

    RED before: the column was unconstrained NUMERIC, so 12.3456 was stored
    verbatim while every other money column in the schema rounds to 2 — and the
    readers then disagreed about what the cost was.
    """
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="m02", name="M02")
        s.add(t)
        await s.flush()
        s.add(Product(tenant_id=t.id, name_uz="p", base_price=Decimal("100.00")))
        await s.commit()

    async with migrated_db.connect() as conn:
        trans = await conn.begin()
        await conn.execute(text("UPDATE products SET cost_price = 12.3456"))
        stored = (await conn.execute(text("SELECT cost_price FROM products"))).scalar_one()
        await trans.rollback()
    assert stored == Decimal("12.35")


# ─────────────────────────────────────────────────────────────────────────────
# M-03 — r68's downgrade must not round money away in silence
# ─────────────────────────────────────────────────────────────────────────────


def _load_r68():
    """Load the r68 migration module by path — ``alembic/versions`` is a script
    directory, not an importable package."""
    import importlib.util
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "alembic" / "versions" / "r68_tenant_balance_numeric.py"
    )
    spec = importlib.util.spec_from_file_location("_r68_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _tenant_with_balance(engine, slug: str, balance: Decimal) -> None:
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        s.add(Tenant(slug=slug, name=slug.upper(), balance=balance))
        await s.commit()


def _run_r68_downgrade(sync_conn) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    ctx = MigrationContext.configure(sync_conn)
    with Operations.context(ctx):
        _load_r68().downgrade()


async def test_m03_r68_downgrade_refuses_rather_than_rounding_money(migrated_db) -> None:
    """RED before: the downgrade succeeded and turned 100.75 so'm into 101.

    ``numeric::integer`` ROUNDS in Postgres, and no downgrade path recomputes the
    balances, so the rollback silently changed what a tenant is owed. This asserts
    the two acceptable outcomes (refuse, or leave the value alone) and rules out
    the old one (mutate the money and report success).
    """
    await _tenant_with_balance(migrated_db, "m03", Decimal("100.75"))

    async with migrated_db.connect() as conn:
        trans = await conn.begin()
        try:
            with pytest.raises(RuntimeError, match="fractional"):
                await conn.run_sync(_run_r68_downgrade)
        finally:
            await trans.rollback()

    async with migrated_db.connect() as conn:
        balance = (await conn.execute(
            text("SELECT balance FROM tenants WHERE slug = 'm03'")
        )).scalar_one()
    assert balance == Decimal("100.75"), "the rollback must not touch the money"


async def test_m03_r68_downgrade_still_works_when_no_row_uses_the_precision(migrated_db) -> None:
    """The guard blocks LOSS, not the legitimate rollback-a-bad-deploy case."""
    await _tenant_with_balance(migrated_db, "m03b", Decimal("100.00"))

    async with migrated_db.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.run_sync(_run_r68_downgrade)   # must NOT raise
            kind = (await conn.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='tenants' AND column_name='balance'"
            ))).scalar_one()
            assert kind == "integer"
        finally:
            await trans.rollback()


# ─────────────────────────────────────────────────────────────────────────────
# M-04 — at most one ACTIVE subscription per tenant, enforced by the database
# ─────────────────────────────────────────────────────────────────────────────


async def _two_subscriptions(engine, statuses: tuple[SubscriptionStatus, SubscriptionStatus]):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug=f"m04-{uuid.uuid4().hex[:6]}", name="M04")
        plan = Plan(
            code=f"p{uuid.uuid4().hex[:6]}", name_uz="P", name_ru="P",
            price_monthly=Decimal("10.00"),
        )
        s.add_all([t, plan])
        await s.flush()
        now = datetime.now(UTC)
        s.add_all([
            TenantSubscription(tenant_id=t.id, plan_id=plan.id, status=statuses[0],
                               starts_at=now, amount=Decimal("10.00")),
            TenantSubscription(tenant_id=t.id, plan_id=plan.id, status=statuses[1],
                               starts_at=now + timedelta(days=1), amount=Decimal("10.00")),
        ])
        await s.commit()


async def test_m04_second_active_subscription_is_refused_by_the_database(migrated_db) -> None:
    """RED before: `uq_tenant_sub_start` only stopped a shared start instant, so
    two ACTIVE rows with different starts committed happily and which one billing
    read was a matter of query order."""
    with pytest.raises(IntegrityError):
        await _two_subscriptions(
            migrated_db, (SubscriptionStatus.active, SubscriptionStatus.active)
        )


async def test_m04_subscription_history_is_still_allowed_to_accumulate(migrated_db) -> None:
    """The constraint is PARTIAL on purpose: cancelled/trial rows ARE the billing record."""
    await _two_subscriptions(
        migrated_db, (SubscriptionStatus.cancelled, SubscriptionStatus.active)
    )
    await _two_subscriptions(
        migrated_db, (SubscriptionStatus.trial, SubscriptionStatus.trial)
    )


# ─────────────────────────────────────────────────────────────────────────────
# M-06 — a Telegram update without a username must not erase the known one
# ─────────────────────────────────────────────────────────────────────────────


def _tg_message(*, tg_id: int, username: str | None, first: str = "Ali") -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(
            id=tg_id, username=username, first_name=first,
            last_name=None, language_code="uz",
        )
    )


async def test_m06_missing_username_preserves_the_known_handle(migrated_db, monkeypatch) -> None:
    """RED before: `user.telegram_username = message.from_user.username` was
    unconditional, so the next update from an account whose handle Telegram
    omitted wrote NULL over the only string support can search a person by."""
    import app.bot.main as bot_main

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)

    @asynccontextmanager
    async def _session(**_kwargs):
        async with Session() as s:
            yield s

    monkeypatch.setattr(bot_main, "worker_session", _session)

    created = await bot_main._ensure_user(_tg_message(tg_id=555001, username="alivali"))
    assert created.telegram_username == "alivali"

    # Same person, update with no username field (Telegram omits it).
    again = await bot_main._ensure_user(_tg_message(tg_id=555001, username=None))
    assert again.telegram_username == "alivali"

    # A REAL new handle still wins — the fix preserves, it does not freeze.
    renamed = await bot_main._ensure_user(_tg_message(tg_id=555001, username="ali_v2"))
    assert renamed.telegram_username == "ali_v2"


# ─────────────────────────────────────────────────────────────────────────────
# LOW batch
# ─────────────────────────────────────────────────────────────────────────────


async def test_low1_set_tenant_status_rejects_a_malformed_id_with_400() -> None:
    """RED before: the raw string went to the query and surfaced as a 500.

    `db=None` is deliberate: a correct handler must refuse before it touches the
    database, exactly as the neighbouring `set_plan_overrides` does.
    """
    from fastapi import HTTPException

    from app.api.platform.provisioning import TenantStatusIn, set_tenant_status

    admin = User(telegram_id=1, platform_role=PlatformRole.super_admin)
    with pytest.raises(HTTPException) as exc:
        await set_tenant_status(
            "not-a-uuid", TenantStatusIn(status="active"), admin, None,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "bad_tenant_id"


async def test_low2_editing_a_contract_rejects_a_foreign_guarantor(isolation_ctx) -> None:
    """RED before: the PUT silently DROPPED unknown guarantor ids and returned 200
    — an edit that did not do what was asked, with nothing on screen saying so.
    Create already answered 404; the two paths now agree."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    body = {
        "customer_id": ctx["customer_a"], "months": 6,
        "items": [{"product_name_snapshot": "Phone", "unit_price": "6000000", "qty": 1}],
    }
    created = await client.post("/api/tenant/installments", headers=auth, json=body)
    assert created.status_code == 201, created.text[:300]
    iid = created.json()["id"]

    stranger = str(uuid.uuid4())
    # The create path's answer, for comparison.
    r_create = await client.post(
        "/api/tenant/installments", headers=auth, json={**body, "guarantor_ids": [stranger]},
    )
    assert r_create.status_code == 404

    r_put = await client.put(
        f"/api/tenant/installments/{iid}", headers=auth,
        json={**body, "guarantor_ids": [stranger]},
    )
    assert r_put.status_code == 404, (
        f"the edit path silently dropped the id and returned {r_put.status_code}"
    )


def test_low3_profile_email_is_validated_but_still_clearable() -> None:
    """RED before: `str | None` let junk reach `users.email`, which is UNIQUE (r72)
    — so a typo took a slot in the identity space instead of bouncing at the edge."""
    from pydantic import ValidationError

    from app.api.auth import ProfileUpdateIn

    with pytest.raises(ValidationError):
        ProfileUpdateIn(email="not-an-email")
    with pytest.raises(ValidationError):
        ProfileUpdateIn(email="two@@at.example")

    assert ProfileUpdateIn(email="owner@shop.uz").email == "owner@shop.uz"
    # "" must still mean "clear it" — update_me's only unset path.
    assert ProfileUpdateIn(email="").email is None
    assert ProfileUpdateIn().email is None


def test_low4_bot_token_length_is_bounded() -> None:
    """RED before: unbounded — and the value is interpolated into the Telegram API
    URL and then encrypted into an `EncryptedStr(500)` column."""
    from pydantic import ValidationError

    from app.api.tenant_settings import BotConfigIn

    real = "123456789:AAF-" + "x" * 31
    assert BotConfigIn(bot_token=real).bot_token == real
    with pytest.raises(ValidationError):
        BotConfigIn(bot_token="x" * 129)


def test_low5_list_page_size_is_bounded_by_the_signature_not_by_hand() -> None:
    """RED before: `me.py` and `sms.py` declared a BARE `int` and re-capped inside
    the body, so an out-of-range page size was silently rewritten instead of refused.

    Asserted through the DECLARED bound (what produces the 422) rather than the
    absence of a `min()` call, so the property survives a rewrite.
    """
    from typing import get_type_hints

    from app.api.me import my_notifications
    from app.api.sms import sms_history

    def _upper_bound(fn, name: str) -> int | None:
        annotation = get_type_hints(fn, include_extras=True)[name]
        for meta in getattr(annotation, "__metadata__", ()):
            for constraint in (meta, *getattr(meta, "metadata", ())):
                if getattr(constraint, "le", None) is not None:
                    return constraint.le
        return None

    assert _upper_bound(my_notifications, "limit") == 100
    assert _upper_bound(sms_history, "limit") == 200


async def test_low5_out_of_range_page_size_is_refused_not_rewritten(isolation_ctx) -> None:
    """The behaviour the bound buys: 422, instead of a cheerful 200 for a request
    the server quietly rewrote into a different one."""
    client, ctx = isolation_ctx
    auth = {**ctx["auth_a"], "X-Tenant-Id": ctx["tenant_a"]}

    ok = await client.get("/api/tenant/sms/history?limit=50", headers=auth)
    assert ok.status_code == 200, ok.text[:300]

    too_big = await client.get("/api/tenant/sms/history?limit=99999", headers=auth)
    assert too_big.status_code == 422, (
        f"an out-of-range page size returned {too_big.status_code}; "
        "the route silently re-capped it instead of refusing"
    )


def test_low5_no_route_re_caps_a_page_size_by_hand() -> None:
    """The acceptance criterion, stated as a test: zero hand-rolled clamps left."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    offenders = [
        f"{p.relative_to(root.parent)}:{n}"
        for p in root.rglob("*.py")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if "min(limit" in line
    ]
    assert offenders == [], f"hand-rolled page clamps still present: {offenders}"


def test_low6_round_amount_docstring_does_not_call_half_up_bankers_rounding() -> None:
    """RED before: the docstring called ROUND_HALF_UP "banker's preferred method".
    Banker's rounding IS ROUND_HALF_EVEN — the opposite tie-break."""
    from app.core.money_utils import round_amount

    doc = round_amount.__doc__ or ""
    assert "banker's preferred" not in doc
    assert "ROUND_HALF_EVEN" in doc, "the docstring must name what it is NOT"
    # And the behaviour it documents: 0.125 -> 0.13 (half-up), not 0.12 (half-even).
    assert round_amount(Decimal("0.125")) == Decimal("0.13")


def test_low7_money_utils_exports_only_the_helper_the_app_uses() -> None:
    """RED before: `split_amount` / `verify_split_sum` were exported and tested but
    called by nothing — a SECOND answer to "how does a total become N payments",
    contradicting `services/calc.compute_schedule` on both the remainder and the scale."""
    import app.core.money_utils as mu

    # Exhaustive on purpose: a NEW export must fail here and force a conscious
    # decision, rather than another splitter drifting in unnoticed. `round_som`
    # (MUDD-65) is admitted because it is a *quantiser* — it rounds one amount to
    # whole so'm — not a second answer to "how does a total become N payments",
    # which remains `services/calc.compute_schedule` alone.
    assert set(mu.__all__) == {"round_amount", "round_som"}
    assert not hasattr(mu, "split_amount")
    assert not hasattr(mu, "verify_split_sum")
