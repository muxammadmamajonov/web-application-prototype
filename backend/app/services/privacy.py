"""Customer data-subject rights — export + erasure (#167).

Backs the Privacy Policy's data-access (export) and erasure promises:

* ``export_customer`` gathers everything linked to a customer into a
  JSON-serializable bundle (the subject's own PII included — it's their data).
* ``erase_customer`` ANONYMIZES the customer in place. Financial records
  (installments/payments) are deliberately preserved (the #100 no-soft-delete
  design keeps them via RESTRICT FKs), but every PII field is wiped, the
  orphan-prone PII tables (SMS, notification prefs, broadcast recipients,
  invitations) are purged, and the customer's private media (KYC + receipt blobs)
  is deleted. Cross-tenant risk signals are already pseudonymized (a salted
  jshshir hash, no plaintext), so they're kept but their back-link to the erased
  customer is severed.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, bytes | bytearray):
        return f"<{len(v)} bytes>"
    return v


def _row(row: Any) -> dict:
    """Serialize an ORM row's columns to a JSON-safe dict. EncryptedStr columns
    decrypt on attribute access — appropriate for the subject's own export.

    Values are read by mapper ATTRIBUTE name and emitted under the DB COLUMN
    name, so a column mapped under a different attribute (``metadata_json`` →
    column ``metadata``, ``before_json`` → ``before``) serializes correctly.
    Iterating ``__table__.columns`` instead would look the column name up as an
    attribute, which is either absent or — for ``metadata`` — the declarative
    ``MetaData`` object.
    """
    mapper = sa_inspect(type(row))
    return {
        attr.columns[0].key: _jsonable(getattr(row, attr.key))
        for attr in mapper.column_attrs
    }


def _maybe_uuid(v: Any) -> UUID | None:
    try:
        return UUID(str(v))
    except (ValueError, TypeError):
        return None


def _receipt_blob_id(url: str | None) -> UUID | None:
    """Parse the ReceiptBlob id out of a ``/api/receipts/<id>?s=<sig>`` URL."""
    if not url or "/api/receipts/" not in url:
        return None
    tail = url.split("/api/receipts/", 1)[1].split("?", 1)[0].strip("/")
    return _maybe_uuid(tail)


async def export_customer(db: AsyncSession, customer: Any) -> dict:
    """A JSON-serializable bundle of everything linked to a customer (DSAR)."""
    from app.models import (
        AuditLog,
        CustomerRiskSignal,
        Guarantor,
        Installment,
        InstallmentGuarantor,
        InstallmentItem,
        Invitation,
        NotificationChannel,
        Payment,
        SmsMessage,
    )

    cid = customer.id

    async def _rows(model: Any, predicate: Any) -> list[dict]:
        return [_row(r) for r in (await db.execute(select(model).where(predicate))).scalars().all()]

    installments = (await db.execute(
        select(Installment).where(Installment.customer_id == cid)
    )).scalars().all()
    ins_ids = [i.id for i in installments]

    items: list[dict] = []
    guarantors: list[dict] = []
    if ins_ids:
        items = await _rows(InstallmentItem, InstallmentItem.installment_id.in_(ins_ids))
        g_ids = (await db.execute(
            select(InstallmentGuarantor.guarantor_id)
            .where(InstallmentGuarantor.installment_id.in_(ins_ids))
        )).scalars().all()
        if g_ids:
            guarantors = await _rows(Guarantor, Guarantor.id.in_(g_ids))

    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "customer": _row(customer),
        "installments": [_row(i) for i in installments],
        "installment_items": items,
        "payments": await _rows(Payment, Payment.customer_id == cid),
        "guarantors": guarantors,
        "sms_messages": await _rows(SmsMessage, SmsMessage.customer_id == cid),
        "notification_channels": await _rows(
            NotificationChannel, NotificationChannel.customer_id == cid
        ),
        "invitations": await _rows(Invitation, Invitation.customer_id == cid),
        "risk_signals": await _rows(
            CustomerRiskSignal, CustomerRiskSignal.source_customer_id == cid
        ),
        "audit_log": await _rows(
            AuditLog, (AuditLog.entity_type == "customer") & (AuditLog.entity_id == str(cid))
        ),
    }


async def erase_customer(db: AsyncSession, customer: Any, *, actor_user_id: UUID | None = None) -> dict:
    """Anonymize a customer in place + purge orphan PII; keep financial records.
    Returns per-table purge counts. Irreversible. The caller commits."""
    from app.models import (
        AuditLog,
        BroadcastRecipient,
        CustomerRiskSignal,
        Invitation,
        KycBlob,
        NotificationChannel,
        Payment,
        ReceiptBlob,
        SmsMessage,
    )
    from app.models.enums import CustomerStatus
    from app.services import audit as audit_svc
    from app.services.receipt_storage import get_receipt_store

    cid = customer.id
    counts: dict[str, int] = {}
    store = get_receipt_store()

    async def _delete_blobs(model: Any, ids: list[UUID]) -> None:
        """Delete private media blobs by id — both the object-store BYTES (when a
        bucket is configured) and the DB rows; tenant-scoped (#167). Deleting only
        the row would orphan the bucket object forever, defeating erasure."""
        if not ids:
            return
        blobs = (await db.execute(
            select(model).where(model.id.in_(ids), model.tenant_id == customer.tenant_id)
        )).scalars().all()
        if store is not None:
            for b in blobs:
                if b.object_key:
                    await store.delete(b.object_key)
        if blobs:
            await db.execute(delete(model).where(model.id.in_([b.id for b in blobs])))

    # 1. delete the customer's private KYC images — bucket bytes AND rows.
    photo_vals = (
        list(customer.passport_photo_urls or [])
        + list(customer.mehnat_daftarchasi_photo_urls or [])
    )
    kyc_ids = [u for u in (_maybe_uuid(v) for v in photo_vals) if u is not None]
    await _delete_blobs(KycBlob, kyc_ids)

    # 2. delete receipt images for the customer's payments + clear the reference.
    pays = (await db.execute(select(Payment).where(Payment.customer_id == cid))).scalars().all()
    rb_ids = [b for b in (_receipt_blob_id(p.receipt_image_url) for p in pays) if b is not None]
    await _delete_blobs(ReceiptBlob, rb_ids)
    for p in pays:
        if p.receipt_image_url:
            p.receipt_image_url = None

    # NB: guarantors are deliberately NOT erased here — a guarantor is a separate
    # data subject that may back OTHER customers' installments, so erasing one
    # customer must not destroy a guarantor still tied to live relationships.
    # Guarantor erasure is its own request (out of scope for customer erasure).

    # 3. purge orphan-prone PII tables for this customer
    for key, model, col in (
        ("sms_messages", SmsMessage, SmsMessage.customer_id),
        ("notification_channels", NotificationChannel, NotificationChannel.customer_id),
        ("broadcast_recipients", BroadcastRecipient, BroadcastRecipient.customer_id),
        ("invitations", Invitation, Invitation.customer_id),
    ):
        res = await db.execute(delete(model).where(col == cid))
        counts[key] = getattr(res, "rowcount", 0) or 0

    # 4. cross-tenant risk signals are pseudonymized already — keep them, but sever
    #    the back-link to this (now-erased) customer.
    await db.execute(
        update(CustomerRiskSignal)
        .where(CustomerRiskSignal.source_customer_id == cid)
        .values(source_customer_id=None)
    )

    # 5. scrub PII out of audit payloads that snapshot this customer
    await db.execute(
        update(AuditLog)
        .where((AuditLog.entity_type == "customer") & (AuditLog.entity_id == str(cid)))
        .values(before_json=None, after_json=None)
    )

    # 6. anonymize the customer row (financial FKs keep the row; the PII is wiped)
    customer.full_name = "[erased]"
    customer.phone = f"erased-{cid.hex[:25]}"   # NOT NULL + unique-per-tenant; 25 hex ≈ collision-free, fits String(32)
    customer.secondary_phone = None
    customer.telegram_id = None
    customer.passport_id = None
    customer.passport_series = None
    customer.passport_number = None
    customer.jshshir = None
    customer.birthdate = None
    customer.passport_photo_urls = []
    customer.mehnat_daftarchasi_photo_urls = []
    customer.employment_place = None
    customer.monthly_income = None
    customer.address = None
    customer.notes = None
    customer.metadata_json = {"erased_at": datetime.now(UTC).isoformat()}
    customer.status = CustomerStatus.blocked

    # 7. record the erasure itself (no PII in the audit row)
    await audit_svc.record(
        db, action="customer.erased", tenant_id=customer.tenant_id,
        entity_type="customer", entity_id=str(cid), actor_user_id=actor_user_id,
        after={"counts": counts},
    )
    await db.flush()
    return counts


async def _purge_media_blobs(
    db: AsyncSession, model: Any, ids: list[UUID | None], tenant_id: UUID,
) -> None:
    """(#172) Delete private media blobs by id — object-store BYTES (when a bucket is
    configured) AND the DB rows, tenant-scoped. Shared by the hard-delete paths so
    deleting a customer/guarantor can't orphan the storage object forever."""
    clean = [i for i in ids if i is not None]
    if not clean:
        return
    from app.services.receipt_storage import get_receipt_store
    store = get_receipt_store()
    blobs = (await db.execute(
        select(model).where(model.id.in_(clean), model.tenant_id == tenant_id)
    )).scalars().all()
    if store is not None:
        for b in blobs:
            if b.object_key:
                await store.delete(b.object_key)
    if blobs:
        await db.execute(delete(model).where(model.id.in_([b.id for b in blobs])))


async def purge_customer_media(db: AsyncSession, customer: Any) -> None:
    """(#172) Delete a customer's KYC photos + receipt images (object-store bytes + DB
    rows) before a hard delete, so the most sensitive PII isn't orphaned in storage.
    Mirrors erase_customer's media purge for the non-anonymizing delete path."""
    from app.models import KycBlob, Payment, ReceiptBlob

    photo_vals = (
        list(customer.passport_photo_urls or [])
        + list(customer.mehnat_daftarchasi_photo_urls or [])
    )
    await _purge_media_blobs(
        db, KycBlob, [_maybe_uuid(v) for v in photo_vals], customer.tenant_id,
    )
    pays = (await db.execute(
        select(Payment).where(Payment.customer_id == customer.id)
    )).scalars().all()
    await _purge_media_blobs(
        db, ReceiptBlob,
        [_receipt_blob_id(p.receipt_image_url) for p in pays], customer.tenant_id,
    )


async def purge_guarantor_media(db: AsyncSession, guarantor: Any) -> None:
    """(#172) Delete a guarantor's KYC photos (object-store bytes + DB rows) before a
    hard delete."""
    from app.models import KycBlob

    await _purge_media_blobs(
        db, KycBlob,
        [_maybe_uuid(v) for v in (guarantor.photo_urls or [])], guarantor.tenant_id,
    )


async def purge_tenant_media(db: AsyncSession, tenant_id: UUID) -> int:
    """(#172) Delete ALL of a tenant's KYC + receipt OBJECTS from object storage before
    the tenant (and its blob rows) are cascade-deleted. The FK cascade frees the DB rows
    but would orphan the bucket objects forever. Returns the count deleted; a no-op when
    no object store is configured (postgres-mode keeps the bytes in the DB)."""
    from app.models import KycBlob, ReceiptBlob
    from app.services.receipt_storage import get_receipt_store

    store = get_receipt_store()
    if store is None:
        return 0
    deleted = 0
    for model in (KycBlob, ReceiptBlob):
        keys = (await db.execute(
            select(model.object_key).where(
                model.tenant_id == tenant_id, model.object_key.is_not(None),
            )
        )).scalars().all()
        for key in keys:
            if key:
                await store.delete(key)
                deleted += 1
    return deleted
