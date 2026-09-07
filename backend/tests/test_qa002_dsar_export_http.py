"""QA-002 — both GDPR DSAR export endpoints returned HTTP 500.

``privacy._row`` built each row from ``row.__table__.columns`` and read every
value with ``getattr(row, column.key)``. That is only correct while the ORM
attribute name and the DB column name coincide. Three mapped models rename one:

    Customer.metadata_json  -> column "metadata"
    AuditLog.before_json    -> column "before"
    AuditLog.after_json     -> column "after"
    Guarantor.relationship_label -> column "relationship"

``getattr(customer, "metadata")`` therefore returned the **declarative
``MetaData`` object** — unserializable, so FastAPI raised at the response
boundary — and the other three raised ``AttributeError`` as soon as a row
existed. Reading through the mapper instead of the table fixes the whole class
at once, with no per-field exception list.

The defect lived *only* at the HTTP serialization boundary: the existing
``test_privacy.py`` test calls ``export_customer`` directly, and a Python dict
holding a ``MetaData`` value is perfectly valid, so it passed throughout. These
tests therefore issue the real HTTP requests.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

TG_SUBJECT, TG_OTHER = 920001, 920002


@pytest_asyncio.fixture
async def dsar_ctx(grant_verified_phone):
    """Two tenants, each with an owner. Tenant A holds one richly-populated
    customer — installment + item + guarantor + payment + SMS + notification
    channel + invitation + risk signal + audit rows — linked to a Telegram user
    who is also the caller of the self-service export.

    Tenant B holds an unrelated customer, so the isolation assertions are real.
    """
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — integration tier runs in CI")

    import httpx
    from httpx import ASGITransport
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import issue_token
    from app.db import Base, get_db
    from app.main import app
    from app.models import (
        AuditLog,
        Customer,
        CustomerRiskSignal,
        Guarantor,
        Installment,
        InstallmentGuarantor,
        InstallmentItem,
        Invitation,
        NotificationChannel,
        Payment,
        SmsMessage,
        Tenant,
        TenantUser,
        User,
    )
    from app.models.enums import (
        CustomerStatus,
        InstallmentStatus,
        NotificationChannelType,
        PaymentMethod,
        PaymentStatus,
        RiskSeverity,
        TenantRole,
    )

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
            ta = Tenant(slug="qa002-a", name="QA002 A")
            tb = Tenant(slug="qa002-b", name="QA002 B")
            s.add_all([ta, tb])
            await s.flush()

            owner_a, owner_b = User(telegram_id=920101), User(telegram_id=920102)
            manager_a, sales_a = User(telegram_id=920103), User(telegram_id=920104)
            subject = User(telegram_id=TG_SUBJECT, first_name="Ali", phone="+998901234567")
            other = User(telegram_id=TG_OTHER, first_name="Bek")
            grant_verified_phone(subject, "+998901234567")
            grant_verified_phone(other)
            s.add_all([owner_a, owner_b, manager_a, sales_a, subject, other])
            await s.flush()
            s.add_all([
                TenantUser(tenant_id=ta.id, user_id=owner_a.id, role=TenantRole.owner),
                TenantUser(tenant_id=ta.id, user_id=manager_a.id, role=TenantRole.manager),
                TenantUser(tenant_id=ta.id, user_id=sales_a.id, role=TenantRole.sales),
                TenantUser(tenant_id=tb.id, user_id=owner_b.id, role=TenantRole.owner),
            ])

            # The subject — every renamed / awkward-typed column populated.
            c = Customer(
                tenant_id=ta.id, full_name="Ali Valiyev", phone="+998901234567",
                telegram_id=TG_SUBJECT, jshshir="12345678901234",
                passport_series="AA", passport_number="1234567",
                birthdate=date(1990, 5, 17),               # date
                monthly_income=Decimal("7500000.50"),      # Decimal
                address="Tashkent", notes="vip",
                status=CustomerStatus.active,              # Enum
                metadata_json={"extra": {"shoe_size": "42"}, "via_invitation": "seed"},
            )
            # Tenant B's unrelated customer, same Telegram id NOT reused.
            cb = Customer(tenant_id=tb.id, full_name="Bek Bekov",
                          phone="+998901234568", telegram_id=TG_OTHER)
            s.add_all([c, cb])
            await s.flush()

            ins = Installment(currency="UZS",
                tenant_id=ta.id, customer_id=c.id, total_amount=Decimal("1000000"),
                principal=Decimal("1000000"), down_payment=Decimal("0"), months=10,
                monthly_amount=Decimal("100000"), paid_amount=Decimal("0"),
                start_date=date(2026, 1, 1), status=InstallmentStatus.active,
            )
            s.add(ins)
            await s.flush()
            s.add(InstallmentItem(
                installment_id=ins.id, product_name_snapshot="Phone",
                unit_price=Decimal("1000000"), qty=1, line_total=Decimal("1000000"),
            ))

            g = Guarantor(
                tenant_id=ta.id, full_name="Vali Aliyev", phone="+998901234599",
                relationship_label="aka",          # column "relationship"
                passport_series="BB", passport_number="7654321",
            )
            s.add(g)
            await s.flush()
            s.add(InstallmentGuarantor(installment_id=ins.id, guarantor_id=g.id, sequence=1))

            s.add(Payment(currency="UZS",
                tenant_id=ta.id, customer_id=c.id, installment_id=ins.id,
                amount=Decimal("100000"), method=PaymentMethod.cash,
                status=PaymentStatus.approved,
            ))
            s.add(SmsMessage(
                tenant_id=ta.id, customer_id=c.id, phone="+998901234567",
                text="To'lov eslatmasi", cost_uzs=Decimal("120.00"),
            ))
            s.add(NotificationChannel(
                tenant_id=ta.id, customer_id=c.id,
                channel=NotificationChannelType.telegram,
            ))
            s.add(Invitation(
                token="QA002INVTK", tenant_id=ta.id, kind="customer",
                customer_id=c.id, expires_at=datetime.now(UTC).replace(year=2030),
            ))
            s.add(CustomerRiskSignal(
                source_tenant_id=ta.id, source_customer_id=c.id,
                jshshir_hash="deadbeef" * 8, severity=RiskSeverity.late_30,
            ))
            # An audit row whose renamed before/after payloads must survive export.
            s.add(AuditLog(
                tenant_id=ta.id, action="customer.updated", entity_type="customer",
                entity_id=str(c.id),
                before_json={"notes": None},
                after_json={"notes": "vip"},
            ))
            await s.commit()
            ctx = {
                "customer_a": str(c.id),
                "customer_b": str(cb.id),
                "owner_a": {"Authorization": f"Bearer {issue_token(user_id=str(owner_a.id))}"},
                "owner_b": {"Authorization": f"Bearer {issue_token(user_id=str(owner_b.id))}"},
                "manager_a": {"Authorization": f"Bearer {issue_token(user_id=str(manager_a.id))}"},
                "sales_a": {"Authorization": f"Bearer {issue_token(user_id=str(sales_a.id))}"},
                "subject": {"Authorization": f"Bearer {issue_token(user_id=str(subject.id))}"},
                "other": {"Authorization": f"Bearer {issue_token(user_id=str(other.id))}"},
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


def _tenant_export(ctx) -> str:
    return f"/api/tenant/customers/{ctx['customer_a']}/export"


ME_EXPORT = "/api/me/privacy/export"


def _assert_json_safe(payload) -> None:
    """The response must be plain JSON — no ORM object, no MetaData, survived."""
    blob = json.dumps(payload)          # raises on anything non-JSON
    for leak in ("MetaData", "sqlalchemy", "InstrumentedAttribute", "<object at 0x"):
        assert leak not in blob, f"{leak!r} leaked into the DSAR bundle"


# ── the 500s ───────────────────────────────────────────────────────────────

async def test_tenant_dsar_export_returns_200(dsar_ctx) -> None:
    client, ctx = dsar_ctx
    r = await client.get(_tenant_export(ctx), headers=ctx["owner_a"])
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    _assert_json_safe(r.json())


async def test_client_dsar_export_returns_200(dsar_ctx) -> None:
    client, ctx = dsar_ctx
    r = await client.get(ME_EXPORT, headers=ctx["subject"])
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    _assert_json_safe(r.json())


# ── the renamed mapped attributes, at the wire ─────────────────────────────

async def test_renamed_columns_export_their_values(dsar_ctx) -> None:
    """The three renamed mappings are the defect's whole substance: each must
    carry its real value under its DB column name."""
    client, ctx = dsar_ctx
    r = await client.get(_tenant_export(ctx), headers=ctx["owner_a"])
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    b = r.json()

    # Customer.metadata_json → "metadata" (was the declarative MetaData object)
    assert b["customer"]["metadata"] == {
        "extra": {"shoe_size": "42"}, "via_invitation": "seed",
    }
    # Guarantor.relationship_label → "relationship" (was AttributeError)
    assert len(b["guarantors"]) == 1
    assert b["guarantors"][0]["relationship"] == "aka"
    # AuditLog.before_json / after_json → "before" / "after" (were AttributeError)
    assert len(b["audit_log"]) == 1
    assert b["audit_log"][0]["before"] == {"notes": None}
    assert b["audit_log"][0]["after"] == {"notes": "vip"}


async def test_awkward_column_types_stay_json_safe(dsar_ctx) -> None:
    """UUID, date, datetime, Decimal and Enum all cross the boundary as JSON."""
    client, ctx = dsar_ctx
    r = await client.get(_tenant_export(ctx), headers=ctx["owner_a"])
    assert r.status_code == 200
    b = r.json()
    cust = b["customer"]
    assert cust["id"] == ctx["customer_a"]                 # UUID → str
    assert cust["birthdate"] == "1990-05-17"               # date → ISO
    assert cust["monthly_income"] == "7500000.50"          # Decimal → str
    assert isinstance(cust["created_at"], str)             # datetime → ISO
    assert cust["status"] == "active"                      # Enum → value
    assert b["sms_messages"][0]["cost_uzs"] == "120.00"
    assert b["payments"][0]["amount"] == "100000.00"
    assert b["installments"][0]["start_date"] == "2026-01-01"


async def test_bundle_covers_every_linked_section(dsar_ctx) -> None:
    """The fix must not silently drop a section while making the payload valid."""
    client, ctx = dsar_ctx
    r = await client.get(_tenant_export(ctx), headers=ctx["owner_a"])
    b = r.json()
    for key in ("installments", "installment_items", "payments", "guarantors",
                "sms_messages", "notification_channels", "invitations",
                "risk_signals", "audit_log"):
        assert len(b[key]) == 1, f"{key} lost its row: {b[key]!r}"
    assert b["customer"]["full_name"] == "Ali Valiyev"
    assert b["customer"]["jshshir"] == "12345678901234"    # subject's own data


async def test_row_serializer_handles_binary_columns(dsar_ctx) -> None:
    """`bytes` columns must degrade to a size marker, never raw binary. Exercised
    on the real ``KycBlob`` mapper (no exported table carries bytes today, so the
    HTTP path cannot cover this)."""
    from app.models import KycBlob
    from app.services.privacy import _row

    blob = KycBlob(content_type="image/webp", data=b"1234567890")
    assert _row(blob)["data"] == "<10 bytes>"


# ── authorization + isolation are unchanged ────────────────────────────────

async def test_tenant_export_stays_owner_only(dsar_ctx) -> None:
    client, ctx = dsar_ctx
    for role in ("manager_a", "sales_a"):
        r = await client.get(_tenant_export(ctx), headers=ctx[role])
        assert r.status_code == 403, f"{role} reached the DSAR export: {r.status_code}"
        assert r.json()["detail"] == "owner_only"


async def test_tenant_export_is_tenant_isolated(dsar_ctx) -> None:
    """Tenant B's owner must not export tenant A's customer."""
    client, ctx = dsar_ctx
    r = await client.get(_tenant_export(ctx), headers=ctx["owner_b"])
    assert r.status_code == 404
    assert r.json()["detail"] == "customer_not_found"


async def test_self_service_export_is_scoped_to_the_caller(dsar_ctx) -> None:
    client, ctx = dsar_ctx
    mine = (await client.get(ME_EXPORT, headers=ctx["subject"])).json()
    assert [shop["customer"]["id"] for shop in mine["shops"]] == [ctx["customer_a"]]
    assert mine["subject"]["telegram_id"] == TG_SUBJECT

    theirs = await client.get(ME_EXPORT, headers=ctx["other"])
    assert theirs.status_code == 200
    assert [shop["customer"]["id"] for shop in theirs.json()["shops"]] == [ctx["customer_b"]]


async def test_both_exports_require_authentication(dsar_ctx) -> None:
    client, ctx = dsar_ctx
    assert (await client.get(_tenant_export(ctx))).status_code == 401
    assert (await client.get(ME_EXPORT)).status_code == 401


async def test_erasure_behaviour_is_untouched(dsar_ctx) -> None:
    """Erasure already worked; the export fix must not disturb it."""
    client, ctx = dsar_ctx
    r = await client.post(
        f"/api/tenant/customers/{ctx['customer_a']}/erase", headers=ctx["owner_a"],
    )
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    body = r.json()
    assert body["erased"] is True
    assert body["purged"]["sms_messages"] == 1
    assert body["purged"]["notification_channels"] == 1
    assert body["purged"]["invitations"] == 1
