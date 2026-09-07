"""A two-tenant world on the REAL migrated schema, for the RLS security suite.

Everything in ``test_rls_*.py`` is judged against this: two shops that do not
know about each other, one person who is a verified customer of shop A, one
person who is not verified at all, and a row in every protected table on both
sides. The point is that a probe can distinguish "the policy filtered it" from
"there was nothing there" — an empty table proves nothing about isolation, and
most of the fail-open findings this batch closes were invisible for exactly that
reason.

The world is built ONCE per test module (migrations take a few seconds) and
every probe runs in a transaction it rolls back, so tests do not have to clean
up after each other. The ``rls_world`` fixture itself lives in ``conftest.py``
so test modules can take it as a parameter without importing it — importing a
fixture into the module that uses it shadows the name and trips F811.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@dataclass
class World:
    """Ids the probes need. No object graphs — ids only, so a probe is a query."""

    engine: object
    tenant_a: str
    tenant_b: str
    owner_a: str
    owner_b: str
    customer_user: str          # verified, a customer of shop A only
    unverified_user: str        # a Telegram account with no proved number
    customer_a: str
    customer_b: str
    installment_a: str
    installment_b: str
    payment_a: str
    payment_b: str
    invitation_a: str
    invitation_b: str
    invitation_verifier_a: str
    notification_id: str
    receipt_a: str
    media_a: str
    tg_customer: int
    tg_unverified: int


def pg_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


async def _migrate(url: str) -> None:
    import asyncio

    from app.config import settings
    from tests._schema_lock import reset_public_schema, run_locked_migration_sync

    # (MUDD-95) Both the schema reset and the upgrade below share the same
    # advisory lock `migrated_db` / `isolation_ctx` take (tests/_schema_lock.py)
    # — this fixture previously ran `alembic upgrade head` with NO lock at all,
    # so its migration could freely interleave with either of theirs and
    # deadlock on the AccessExclusiveLock `ALTER TABLE ... ENABLE ROW LEVEL
    # SECURITY` takes per table (reproduced live on CI run 33167077131).
    engine = create_async_engine(url)
    try:
        await reset_public_schema(engine, settings.migration_url_sync)
    finally:
        await engine.dispose()

    await asyncio.to_thread(run_locked_migration_sync, settings.migration_url_sync)


async def build(url: str) -> World:
    """Migrate a clean schema and seed both shops."""
    from app.core import invite_codes
    from app.models import (
        Customer,
        DocumentDelivery,
        Installment,
        InstallmentItem,
        InstallmentItemIdentifier,
        Invitation,
        KycBlob,
        MediaFile,
        Notification,
        Payment,
        PaymentReviewNotice,
        ReceiptBlob,
        Tenant,
        TenantSettings,
        TenantUser,
        User,
    )
    from app.models.enums import (
        InstallmentStatus,
        PaymentMethod,
        PaymentStatus,
        TenantRole,
    )

    await _migrate(url)
    engine = create_async_engine(url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    tg_customer, tg_unverified = 970000001, 970000002
    raw_code = invite_codes.new_credential()

    async with Session() as s:
        ta = Tenant(slug="rls-a", name="Shop A", status="active", locale="uz")
        tb = Tenant(slug="rls-b", name="Shop B", status="active", locale="uz")
        s.add_all([ta, tb])
        await s.flush()
        s.add_all([TenantSettings(tenant_id=ta.id), TenantSettings(tenant_id=tb.id)])

        owner_a = User(telegram_id=970000010, first_name="Owner A")
        owner_b = User(telegram_id=970000011, first_name="Owner B")
        buyer = User(
            telegram_id=tg_customer, first_name="Buyer",
            verified_phone="+998900000101", phone_eligibility="ELIGIBLE",
            verified_phone_at=datetime.now(UTC), verified_phone_source="telegram_contact",
        )
        stranger = User(telegram_id=tg_unverified, first_name="Stranger")
        s.add_all([owner_a, owner_b, buyer, stranger])
        await s.flush()
        s.add_all([
            TenantUser(tenant_id=ta.id, user_id=owner_a.id, role=TenantRole.owner,
                       is_active=True),
            TenantUser(tenant_id=tb.id, user_id=owner_b.id, role=TenantRole.owner,
                       is_active=True),
        ])

        ca = Customer(tenant_id=ta.id, full_name="Alice", phone="+998900000101",
                      telegram_id=tg_customer)
        cb = Customer(tenant_id=tb.id, full_name="Bob", phone="+998900000102",
                      telegram_id=None)
        s.add_all([ca, cb])
        await s.flush()

        today = date.today()

        def _inst(tenant, customer) -> Installment:
            return Installment(
                tenant_id=tenant, customer_id=customer, currency="UZS",
                total_amount=Decimal("1200000"), principal=Decimal("1200000"),
                down_payment=Decimal("0"), months=12,
                monthly_amount=Decimal("100000"), paid_amount=Decimal("0"),
                start_date=today, status=InstallmentStatus.active,
                next_due_date=today + timedelta(days=30),
            )

        ia, ib = _inst(ta.id, ca.id), _inst(tb.id, cb.id)
        s.add_all([ia, ib])
        await s.flush()
        item_a = InstallmentItem(installment_id=ia.id, product_name_snapshot="Phone A",
                                 qty=1, unit_price=Decimal("1200000"),
                                 line_total=Decimal("1200000"))
        item_b = InstallmentItem(installment_id=ib.id, product_name_snapshot="Phone B",
                                 qty=1, unit_price=Decimal("1200000"),
                                 line_total=Decimal("1200000"))
        s.add_all([item_a, item_b])
        await s.flush()
        # (MUDD-85) installment_item_identifiers has its OWN tenant_id (unlike
        # its parent, which is TENANT_CHILD) — one row per shop, or the write
        # matrix's clone probe has nothing to clone (see EMPTY_FOR_TENANT_A).
        s.add_all([
            InstallmentItemIdentifier(tenant_id=ta.id, item_id=item_a.id,
                                      key="imei1", value="352099001761481"),
            InstallmentItemIdentifier(tenant_id=tb.id, item_id=item_b.id,
                                      key="imei1", value="490154203237518"),
        ])

        pa = Payment(tenant_id=ta.id, installment_id=ia.id, customer_id=ca.id,
                     amount=Decimal("100000"), method=PaymentMethod.cash,
                     status=PaymentStatus.approved, currency="UZS")
        pb = Payment(tenant_id=tb.id, installment_id=ib.id, customer_id=cb.id,
                     amount=Decimal("100000"), method=PaymentMethod.cash,
                     status=PaymentStatus.approved, currency="UZS")
        s.add_all([pa, pb])
        await s.flush()

        expires = datetime.now(UTC) + timedelta(days=7)
        inv_a = Invitation(tenant_id=ta.id, kind="customer", expires_at=expires,
                           token_verifier=invite_codes.verifier(raw_code))
        inv_b = Invitation(tenant_id=tb.id, kind="customer", expires_at=expires,
                           token_verifier=invite_codes.verifier(
                               invite_codes.new_credential()))
        s.add_all([inv_a, inv_b])

        notif = Notification(user_id=buyer.id, tenant_id=ta.id, kind="reminder",
                             title="Payment due")
        s.add(notif)

        ra = ReceiptBlob(tenant_id=ta.id, content_type="image/webp", data=b"A")
        rb = ReceiptBlob(tenant_id=tb.id, content_type="image/webp", data=b"B")
        ma = MediaFile(tenant_id=ta.id, bot_owner="platform", status="stored",
                       mime_type="image/webp", file_id="FA")
        mb = MediaFile(tenant_id=tb.id, bot_owner="platform", status="stored",
                       mime_type="image/webp", file_id="FB")
        s.add_all([ra, rb, ma, mb])
        s.add_all([
            KycBlob(tenant_id=ta.id, content_type="image/webp", data=b"A"),
            KycBlob(tenant_id=tb.id, content_type="image/webp", data=b"B"),
        ])
        s.add_all([
            DocumentDelivery(tenant_id=ta.id, user_id=buyer.id, purpose="user_delivery",
                             kind="contract", object_id=ia.id, document_version="v1"),
            DocumentDelivery(tenant_id=tb.id, user_id=owner_b.id, purpose="user_delivery",
                             kind="contract", object_id=ib.id, document_version="v1"),
        ])
        s.add_all([
            PaymentReviewNotice(payment_id=pa.id, tenant_id=ta.id, chat_id=1,
                                message_id=1, state="pending"),
            PaymentReviewNotice(payment_id=pb.id, tenant_id=tb.id, chat_id=2,
                                message_id=2, state="pending"),
        ])
        # ── one row per remaining protected table, for BOTH shops ────────
        # Not padding. Every isolation assertion in the matrix — read AND write
        # — is vacuous against an empty table, and that is not a hypothetical:
        # it is exactly how 46 fail-open policies survived a green suite. A row
        # on each side is what lets a probe distinguish "the policy filtered it"
        # from "there was nothing there".
        from app.models import (
            AuditLog,
            Broadcast,
            CatalogBrand,
            CatalogModel,
            ConsentRecord,
            Guarantor,
            Invoice,
            NotificationChannel,
            Plan,
            Product,
            SmsMessage,
            StockMovement,
            TenantBalanceTxn,
            TenantPaymentProvider,
            TenantSubscription,
            TenantTopup,
        )

        # `app.models.TenantRole` is the ENUM; the table's model lives in
        # app.models.invitation under the same name. `tenant_staff_invitations`
        # has no model at all, which is why it stays unseeded.
        from app.models.invitation import TenantRole as TenantRoleRow

        plan = Plan(code="rls-plan", name_uz="RLS", name_ru="RLS",
                    price_monthly=Decimal("0"))
        brand = CatalogBrand(slug="rls-brand", name="RLS Brand")
        s.add_all([plan, brand])
        await s.flush()

        now = datetime.now(UTC)
        for tag, tenant, customer in (("a", ta.id, ca.id), ("b", tb.id, cb.id)):
            product = Product(tenant_id=tenant, name_uz=f"Product {tag}")
            s.add(product)
            await s.flush()
            s.add_all([
                product,
                AuditLog(tenant_id=tenant, action="rls.seed"),
                Broadcast(tenant_id=tenant, title=f"B{tag}", body_uz="hi"),
                CatalogModel(tenant_id=tenant, brand_id=brand.id,
                             slug=f"rls-model-{tag}", name=f"Model {tag}"),
                ConsentRecord(
                    tenant_id=tenant, subject_kind="customer", subject_id=str(customer),
                    kind="terms", terms_version="v1", lawful_basis="contract",
                    accepted_at=now,
                ),
                Guarantor(tenant_id=tenant, full_name=f"Guarantor {tag}",
                          phone=f"+99890000020{tag == 'b'}"),
                Invoice(tenant_id=tenant, plan_code="rls-plan",
                        period_start=today, period_end=today + timedelta(days=30),
                        issued_at=now, due_at=now + timedelta(days=7)),
                NotificationChannel(tenant_id=tenant, customer_id=customer,
                                    channel="telegram"),
                SmsMessage(tenant_id=tenant, phone=f"+99890000021{tag == 'b'}",
                           text="hello"),
                StockMovement(tenant_id=tenant, product_id=product.id, qty=1,
                              kind="in"),
                TenantBalanceTxn(tenant_id=tenant, amount=Decimal("1000"),
                                 kind="topup", balance_after=Decimal("1000")),
                TenantPaymentProvider(tenant_id=tenant, provider="payme",
                                      merchant_id=f"m-{tag}",
                                      secret_key_ciphertext="x"),
                TenantRoleRow(tenant_id=tenant, slug=f"role-{tag}", name=f"Role {tag}"),
                TenantSubscription(tenant_id=tenant, plan_id=plan.id, starts_at=now),
                TenantTopup(tenant_id=tenant, amount=Decimal("1000"), method="card"),
            ])
        await s.commit()

        return World(
            engine=engine,
            tenant_a=str(ta.id), tenant_b=str(tb.id),
            owner_a=str(owner_a.id), owner_b=str(owner_b.id),
            customer_user=str(buyer.id), unverified_user=str(stranger.id),
            customer_a=str(ca.id), customer_b=str(cb.id),
            installment_a=str(ia.id), installment_b=str(ib.id),
            payment_a=str(pa.id), payment_b=str(pb.id),
            invitation_a=str(inv_a.id), invitation_b=str(inv_b.id),
            invitation_verifier_a=raw_code,
            notification_id=str(notif.id),
            receipt_a=str(ra.id), media_a=str(ma.id),
            tg_customer=tg_customer, tg_unverified=tg_unverified,
        )


async def probe(
    world: World, role: str, sql: str, *,
    tenant: str | None = None, user: str | None = None,
    invitation: str | None = None, params: dict | None = None,
):
    """Run ``sql`` as ``role`` with the given scope, then roll everything back.

    Every GUC is written on every probe — blank when not given — which mirrors
    what ``app.db`` does at transaction start and is what makes "unset", "blank"
    and "left over from the last request" the same state here as in production.
    """
    async with world.engine.connect() as conn:
        tr = await conn.begin()
        try:
            await conn.execute(text(f"SET LOCAL ROLE {role}"))
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :v, true)"),
                {"v": tenant or ""},
            )
            await conn.execute(
                text("SELECT set_config('app.user_id', :v, true)"), {"v": user or ""},
            )
            await conn.execute(
                text("SELECT set_config('app.invitation_id', :v, true)"),
                {"v": invitation or ""},
            )
            return (await conn.execute(text(sql), params or {})).fetchall()
        finally:
            await tr.rollback()


async def count(world: World, role: str, table: str, **scope) -> int:
    rows = await probe(world, role, f"SELECT count(*) FROM {table}", **scope)
    return int(rows[0][0])


async def denied(world: World, role: str, sql: str, *, params: dict | None = None,
                 **scope) -> bool:
    """True when ``sql`` is refused — by a policy, by a privilege, or by matching
    no row. All three are the same security answer: the write did not happen."""
    from sqlalchemy.exc import DBAPIError

    async with world.engine.connect() as conn:
        tr = await conn.begin()
        try:
            await conn.execute(text(f"SET LOCAL ROLE {role}"))
            for guc, key in (("app.tenant_id", "tenant"), ("app.user_id", "user"),
                             ("app.invitation_id", "invitation")):
                await conn.execute(
                    text(f"SELECT set_config('{guc}', :v, true)"),
                    {"v": scope.get(key) or ""},
                )
            result = await conn.execute(text(sql), params or {})
            return result.rowcount == 0
        except DBAPIError:
            return True
        finally:
            await tr.rollback()


def new_uuid() -> str:
    return str(uuid4())


#: SQL that clones a row the caller CAN see into another tenant, generically.
#:
#: The write half of the matrix has to run over every protected table, and every
#: table has a different set of NOT NULL columns — so a hand-written INSERT per
#: table is not a thing anyone maintains. This takes a row the scoped caller is
#: already allowed to read, rewrites its ``id`` and ``tenant_id`` through jsonb,
#: and offers the result back: a valid row for that table by construction, whose
#: only fault is belonging to somebody else. ``WITH CHECK`` is the only thing
#: standing between it and the table.
#: Every parameter is CAST explicitly: asyncpg sends untyped placeholders, and
#: inside `jsonb_build_object` Postgres cannot infer what they are.
CLONE_INTO_OTHER_TENANT = """
INSERT INTO {table}
SELECT (jsonb_populate_record(NULL::{table},
        to_jsonb(x) || jsonb_build_object(
            'id', gen_random_uuid(),
            'tenant_id', CAST(:b AS text)))).*
  FROM (SELECT * FROM {table} WHERE tenant_id = CAST(:a AS uuid) LIMIT 1) x
"""


async def refused_by_policy(
    world: World, role: str, sql: str, *, params: dict | None = None, **scope,
) -> tuple[bool, str]:
    """``(refused, why)`` — and *why* matters.

    ``denied()`` treats every database error as a refusal, which is right for a
    boundary test and wrong for evidence: a unique-constraint violation would
    make a broken policy look enforced. This separates the two, so a write test
    can insist the refusal came from row security rather than from luck.
    """
    from sqlalchemy.exc import DBAPIError

    async with world.engine.connect() as conn:
        tr = await conn.begin()
        try:
            await conn.execute(text(f"SET LOCAL ROLE {role}"))
            for guc, key in (("app.tenant_id", "tenant"), ("app.user_id", "user"),
                             ("app.invitation_id", "invitation")):
                await conn.execute(
                    text(f"SELECT set_config('{guc}', :v, true)"),
                    {"v": scope.get(key) or ""},
                )
            result = await conn.execute(text(sql), params or {})
            return (result.rowcount == 0, "no rows matched")
        except DBAPIError as exc:
            message = str(exc).lower()
            if "row-level security" in message or "row level security" in message:
                return (True, "row-level security")
            if "permission denied" in message:
                return (True, "privilege")
            return (False, f"other error: {type(exc.orig).__name__}")
        finally:
            await tr.rollback()
