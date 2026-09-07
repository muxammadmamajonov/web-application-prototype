"""#167 — customer data export + erasure (data-subject rights).

Export gathers everything linked to a customer (the Privacy Policy's data-access
promise). Erasure ANONYMIZES the customer in place — financial records
(installments/payments) survive by the #100 no-soft-delete design, but all PII is
removed and the orphan-prone PII tables (SMS, notification prefs, broadcast
recipients, invitations) are purged. Cross-tenant risk signals are pseudonymized
already, so they're kept but their back-link to the erased customer is severed.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


async def _seed_customer_with_data(engine):
    from app.api.receipts import receipt_url
    from app.models import (
        Customer,
        CustomerRiskSignal,
        Installment,
        KycBlob,
        Payment,
        ReceiptBlob,
        SmsMessage,
        Tenant,
    )
    from app.models.enums import (
        CustomerStatus,
        InstallmentStatus,
        PaymentMethod,
        PaymentStatus,
        RiskSeverity,
    )

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="erase-t", name="Erase T")
        s.add(t)
        await s.flush()
        kyc = KycBlob(tenant_id=t.id, content_type="image/webp", data=b"img-bytes")
        s.add(kyc)
        await s.flush()
        c = Customer(
            tenant_id=t.id, full_name="Ali Valiyev", phone="+998901234567",
            jshshir="12345678901234", passport_series="AA", passport_number="1234567",
            address="Tashkent", notes="vip", passport_photo_urls=[str(kyc.id)],
            status=CustomerStatus.active,
        )
        s.add(c)
        await s.flush()
        ins = Installment(currency="UZS",
            tenant_id=t.id, customer_id=c.id, total_amount=Decimal("1000000"),
            principal=Decimal("1000000"), down_payment=Decimal("0"), months=10,
            monthly_amount=Decimal("100000"), paid_amount=Decimal("0"),
            start_date=date(2026, 1, 1), status=InstallmentStatus.active,
        )
        s.add(ins)
        await s.flush()
        rb = ReceiptBlob(tenant_id=t.id, content_type="image/webp", data=b"rcpt-bytes")
        s.add(rb)
        await s.flush()
        pay = Payment(currency="UZS",
            tenant_id=t.id, installment_id=ins.id, customer_id=c.id, amount=Decimal("100000"),
            method=PaymentMethod.cash, status=PaymentStatus.approved,
            receipt_image_url=receipt_url(str(rb.id)),
        )
        s.add(pay)
        s.add(SmsMessage(tenant_id=t.id, customer_id=c.id, phone="+998901234567", text="reminder"))
        s.add(CustomerRiskSignal(
            jshshir_hash="hashed", severity=RiskSeverity.late_30, source_customer_id=c.id,
        ))
        await s.commit()
        return t.id, c.id, kyc.id, rb.id, ins.id, pay.id


async def test_export_customer_includes_pii_and_links(migrated_db) -> None:
    from app.models import Customer
    from app.services.privacy import export_customer

    _, cid, *_ = await _seed_customer_with_data(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        c = await s.get(Customer, cid)
        bundle = await export_customer(s, c)
    assert bundle["customer"]["full_name"] == "Ali Valiyev"
    assert bundle["customer"]["jshshir"] == "12345678901234"   # the subject's own data
    assert len(bundle["installments"]) == 1
    assert len(bundle["payments"]) == 1
    assert len(bundle["sms_messages"]) == 1


async def test_erase_customer_anonymizes_purges_and_keeps_financials(migrated_db) -> None:
    from app.models import (
        Customer,
        CustomerRiskSignal,
        Installment,
        KycBlob,
        Payment,
        ReceiptBlob,
        SmsMessage,
    )
    from app.models.enums import CustomerStatus
    from app.services.privacy import erase_customer

    _, cid, kyc_id, rb_id, ins_id, pay_id = await _seed_customer_with_data(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        c = await s.get(Customer, cid)
        await erase_customer(s, c)
        await s.commit()

    async with Session() as s:
        c = await s.get(Customer, cid)
        # PII anonymized in place
        assert c.full_name == "[erased]"
        assert c.phone.startswith("erased-")
        assert c.jshshir is None and c.passport_series is None and c.passport_number is None
        assert c.address is None and c.notes is None and c.secondary_phone is None
        assert c.passport_photo_urls == []
        assert c.status == CustomerStatus.blocked
        # private media deleted
        assert await s.get(KycBlob, kyc_id) is None
        assert await s.get(ReceiptBlob, rb_id) is None
        # orphan-prone PII purged
        assert (await s.execute(
            select(SmsMessage).where(SmsMessage.customer_id == cid)
        )).first() is None
        # risk signal KEPT (pseudonymized) but back-link severed
        rs = (await s.execute(select(CustomerRiskSignal))).scalars().all()
        assert len(rs) == 1 and rs[0].source_customer_id is None
        # FINANCIAL records kept; only the receipt image reference is cleared
        assert await s.get(Installment, ins_id) is not None
        pay = await s.get(Payment, pay_id)
        assert pay is not None and pay.receipt_image_url is None


async def test_erase_deletes_object_store_bytes(migrated_db, monkeypatch) -> None:
    """#167/C1 — when a private bucket is configured, erasure must delete the
    actual object bytes, not just the KycBlob/ReceiptBlob row."""
    from app.models import Customer, KycBlob, Tenant
    from app.models.enums import CustomerStatus
    from app.services.privacy import erase_customer

    deleted: list[str] = []

    class _FakeStore:
        backend = "fake"

        async def put(self, k, d, c):
            ...

        async def get(self, k):
            return None

        async def delete(self, k):
            deleted.append(k)

    import app.services.receipt_storage as rs
    monkeypatch.setattr(rs, "get_receipt_store", lambda: _FakeStore())

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="obj-t", name="O")
        s.add(t)
        await s.flush()
        kyc = KycBlob(tenant_id=t.id, content_type="image/webp", object_key="kyc/secret.webp")
        s.add(kyc)
        await s.flush()
        c = Customer(
            tenant_id=t.id, full_name="X", phone="+998900000001",
            passport_photo_urls=[str(kyc.id)], status=CustomerStatus.active,
        )
        s.add(c)
        await s.flush()
        kid = kyc.id
        await erase_customer(s, c)
        await s.commit()
    assert "kyc/secret.webp" in deleted          # bucket bytes deleted, not just the row
    async with Session() as s:
        assert await s.get(KycBlob, kid) is None


async def test_erase_writes_audit_row(migrated_db) -> None:
    """#167/M1 — the irreversible erasure must leave an audit trail."""
    from app.models import AuditLog, Customer
    from app.services.privacy import erase_customer

    _, cid, *_ = await _seed_customer_with_data(migrated_db)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        c = await s.get(Customer, cid)
        await erase_customer(s, c)
        await s.commit()
    async with Session() as s:
        rows = (await s.execute(
            select(AuditLog).where(AuditLog.action == "customer.erased")
        )).scalars().all()
    assert len(rows) == 1 and rows[0].entity_id == str(cid)
