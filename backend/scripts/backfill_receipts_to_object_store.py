"""One-time, idempotent backfill: move receipt image bytes out of Postgres into
object storage (#96).

Migration r74 adds ``receipt_blobs.object_key`` and makes ``data`` nullable; the
upload path writes NEW receipts to the configured object store automatically.
Run this once after switching ``RECEIPT_STORAGE_BACKEND=supabase`` to move the
receipts written *before* the switch (legacy Postgres blobs):

    python -m scripts.backfill_receipts_to_object_store              # move + free bytes
    python -m scripts.backfill_receipts_to_object_store --dry-run    # report only
    python -m scripts.backfill_receipts_to_object_store --keep-blob  # copy, keep DB bytes

Idempotent: only rows with ``data IS NOT NULL AND object_key IS NULL`` are
processed, and setting ``object_key`` excludes a row from the next pass — so
re-runs are safe and resumable. Requires the object store to be configured
(``RECEIPT_STORAGE_BACKEND`` + ``SUPABASE_SERVICE_KEY``); otherwise it's a no-op.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import ReceiptBlob
from app.services.receipt_storage import get_receipt_store, receipt_object_key

_BATCH = 50


async def _run(*, dry_run: bool, keep_blob: bool) -> int:
    store = get_receipt_store()
    if store is None:
        print(
            "No object store configured (RECEIPT_STORAGE_BACKEND=postgres) — nothing "
            "to do. Set RECEIPT_STORAGE_BACKEND=supabase + SUPABASE_SERVICE_KEY first."
        )
        return 0

    async with SessionLocal() as db:
        pending = (await db.execute(
            select(func.count()).select_from(ReceiptBlob)
            .where(ReceiptBlob.data.isnot(None), ReceiptBlob.object_key.is_(None))
        )).scalar_one()
    print(f"{pending} legacy receipt blob(s) to migrate "
          f"(backend={store.backend}, dry_run={dry_run}, keep_blob={keep_blob})")
    if dry_run or pending == 0:
        return 0

    await store.ensure_bucket()  # type: ignore[attr-defined]
    moved = errors = 0
    while True:
        async with SessionLocal() as db:
            rows = (await db.execute(
                select(ReceiptBlob)
                .where(ReceiptBlob.data.isnot(None), ReceiptBlob.object_key.is_(None))
                .limit(_BATCH)
            )).scalars().all()
            if not rows:
                break
            for blob in rows:
                key = receipt_object_key(blob.id, blob.content_type)
                try:
                    assert blob.data is not None
                    await store.put(key, blob.data, blob.content_type)
                    blob.object_key = key
                    if not keep_blob:
                        blob.data = None  # free the bytes from Postgres
                    moved += 1
                except Exception as exc:  # report + continue, don't abort the batch
                    print(f"  ERROR blob {blob.id}: {exc}")
                    errors += 1
            await db.commit()
        print(f"  …migrated {moved} so far")
    print(f"done: moved={moved} errors={errors}")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill receipt blobs → object storage (#96)")
    ap.add_argument("--dry-run", action="store_true", help="report counts, write nothing")
    ap.add_argument("--keep-blob", action="store_true",
                    help="copy to object storage but keep the Postgres bytes (safer first pass)")
    args = ap.parse_args()
    errors = asyncio.run(_run(dry_run=args.dry_run, keep_blob=args.keep_blob))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
