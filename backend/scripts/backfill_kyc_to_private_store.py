"""Backfill legacy public KYC photos → private store (#113).

Customer/guarantor passport/ID/employment scans used to be uploaded to the public
archive.org ``opensource_media`` collection (the #107 proxy hid the URL but the
bytes stayed world-readable). This script walks every customer/guarantor KYC photo
list, and for each value that is still a public ``http(s)://`` URL:

  1. downloads the bytes,
  2. sanitizes them (EXIF/GPS stripped, sniffed) and stores them PRIVATELY as a
     ``KycBlob`` (private bucket if configured, else Postgres),
  3. replaces the public URL in the list with the new private blob id.

After it reports success you must STILL request takedown of the migrated items
from archive.org (their API has no reliable programmatic delete) — the URLs are
logged so you can submit them. Re-runnable: already-migrated values (non-http blob
ids) are skipped.

Usage:
    uv run python -m scripts.backfill_kyc_to_private_store           # dry run
    uv run python -m scripts.backfill_kyc_to_private_store --commit  # do it
"""
from __future__ import annotations

import asyncio
import sys

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Customer, Guarantor
from app.services.media import store_kyc_image

_KYC_FIELDS = {
    Customer: ("passport_photo_urls", "mehnat_daftarchasi_photo_urls"),
    Guarantor: ("photo_urls",),
}


def _is_public_url(v: str) -> bool:
    return bool(v) and v.startswith(("http://", "https://"))


async def _migrate_row(session, row, fields, *, commit: bool) -> tuple[int, list[str]]:
    migrated = 0
    leftover_public: list[str] = []
    for field in fields:
        urls = getattr(row, field, None) or []
        new_list: list[str] = []
        changed = False
        for v in urls:
            if not _is_public_url(v):
                new_list.append(v)
                continue
            leftover_public.append(v)
            try:
                async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                    resp = await client.get(v)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                print(f"  ! fetch failed {v}: {exc}", file=sys.stderr)
                new_list.append(v)  # keep the old URL; proxy still serves it
                continue
            if not commit:
                new_list.append(v)
                migrated += 1
                continue
            key = await store_kyc_image(
                session, resp.content, resp.headers.get("content-type"), tenant_id=row.tenant_id,
            )
            new_list.append(key)
            migrated += 1
            changed = True
        if changed:
            setattr(row, field, new_list)
    return migrated, leftover_public


async def main(commit: bool) -> None:
    Session = SessionLocal
    total = 0
    public_urls: list[str] = []
    async with Session() as session:
        for model, fields in _KYC_FIELDS.items():
            rows = (await session.execute(select(model))).scalars().all()
            for row in rows:
                n, leftover = await _migrate_row(session, row, fields, commit=commit)
                total += n
                public_urls.extend(leftover)
            if commit:
                await session.commit()
    verb = "migrated" if commit else "would migrate"
    print(f"{verb} {total} KYC photo(s) to the private store.")
    if public_urls:
        print("\nRequest archive.org takedown for these now-migrated public URLs:")
        for u in sorted(set(public_urls)):
            print(f"  {u}")
    if not commit:
        print("\n(dry run — re-run with --commit to apply)")


if __name__ == "__main__":
    asyncio.run(main(commit="--commit" in sys.argv))
