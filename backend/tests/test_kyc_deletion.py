"""(#172) Hard-deleting a customer/guarantor purges their KYC + receipt media from
object storage (not just the DB rows), via purge_customer_media / purge_guarantor_media."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Customer, Guarantor, KycBlob, ReceiptBlob, Tenant
from app.services import privacy


class _FakeStore:
    backend = "fake"

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str): ...
    async def delete(self, key: str) -> None:
        self.deleted.append(key)


async def test_purge_customer_media_deletes_kyc_object_and_row(migrated_db, monkeypatch) -> None:
    fake = _FakeStore()
    monkeypatch.setattr("app.services.receipt_storage.get_receipt_store", lambda: fake)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="purgeco", name="Purge Co")
        s.add(t)
        await s.flush()
        blob = KycBlob(id=uuid4(), tenant_id=t.id, content_type="image/webp", object_key="kyc/x.webp")
        s.add(blob)
        await s.flush()
        c = Customer(tenant_id=t.id, full_name="Del Me", phone="+998900000009",
                     passport_photo_urls=[str(blob.id)])
        s.add(c)
        await s.flush()

        await privacy.purge_customer_media(s, c)
        await s.commit()

        assert fake.deleted == ["kyc/x.webp"]                       # object-store byte delete invoked
        gone = (await s.execute(select(KycBlob).where(KycBlob.id == blob.id))).scalar_one_or_none()
        assert gone is None                                         # DB row removed


async def test_purge_guarantor_media_deletes_kyc(migrated_db, monkeypatch) -> None:
    fake = _FakeStore()
    monkeypatch.setattr("app.services.receipt_storage.get_receipt_store", lambda: fake)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="gpurgeco", name="G Purge Co")
        s.add(t)
        await s.flush()
        blob = KycBlob(id=uuid4(), tenant_id=t.id, content_type="image/webp", object_key="kyc/g.webp")
        s.add(blob)
        await s.flush()
        g = Guarantor(tenant_id=t.id, full_name="Guar Antor", phone="+998900000010",
                      photo_urls=[str(blob.id)])
        s.add(g)
        await s.flush()

        await privacy.purge_guarantor_media(s, g)
        await s.commit()

        assert fake.deleted == ["kyc/g.webp"]
        assert (await s.execute(select(KycBlob).where(KycBlob.id == blob.id))).scalar_one_or_none() is None


async def test_purge_tenant_media_deletes_all_objects(migrated_db, monkeypatch) -> None:
    """(#172 c2) Deleting a tenant purges ALL its KYC + receipt object-store objects."""
    fake = _FakeStore()
    monkeypatch.setattr("app.services.receipt_storage.get_receipt_store", lambda: fake)
    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        t = Tenant(slug="tpurgeco", name="T Purge Co")
        s.add(t)
        await s.flush()
        s.add_all([
            KycBlob(id=uuid4(), tenant_id=t.id, content_type="image/webp", object_key="kyc/a.webp"),
            ReceiptBlob(id=uuid4(), tenant_id=t.id, content_type="image/webp", object_key="receipts/b.webp"),
            KycBlob(id=uuid4(), tenant_id=t.id, content_type="image/webp", object_key=None),  # postgres-mode, no object
        ])
        await s.commit()

        n = await privacy.purge_tenant_media(s, t.id)
        assert n == 2                                                   # only the 2 with an object_key
        assert sorted(fake.deleted) == ["kyc/a.webp", "receipts/b.webp"]
