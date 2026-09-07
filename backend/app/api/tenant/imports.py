"""Tenant back-office: spreadsheet import of products and customers, and customer
broadcasts."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import broadcast_limit, import_limit
from app.core.security import current_user
from app.db import get_db
from app.models import (
    Broadcast,
    Customer,
    Product,
    Tenant,
    TenantUser,
    User,
)
from app.models.enums import (
    BroadcastStatus,
    BroadcastTarget,
    CustomerStatus,
    StockMovementKind,
)
from app.schemas.common import OffsetQ

from ._shared import (
    CustomerIn,
    _derive_passport_id,
    _require_owner_or_manager,
    resolve_tenant,
)

router = APIRouter()

# ── Excel / CSV import (Round 28) ───────────────────────────────────────────

def _parse_upload(filename: str, raw: bytes) -> tuple[list[str], list[dict]]:
    """Parse an uploaded xlsx/csv into ``(headers, rows-as-dicts)``.

    First row is treated as the header. Values are stringified; empty cells
    become ``""``. Raises HTTPException(400) on unsupported / malformed input.
    """
    import csv
    import io

    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = raw.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text))
        table = [row for row in reader]
    elif name.endswith(".xlsx"):
        from zipfile import BadZipFile

        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException

        try:
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            try:
                ws = wb.active
                table = [
                    ["" if c is None else str(c) for c in row]
                    for row in ws.iter_rows(values_only=True)
                ]
            finally:
                wb.close()
        except (BadZipFile, InvalidFileException, KeyError, OSError, ValueError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "malformed_file") from None
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_format")

    table = [r for r in table if any(str(c).strip() for c in r)]
    if not table:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_file")

    headers = [str(h).strip() for h in table[0]]
    rows: list[dict] = []
    for raw_row in table[1:]:
        row = {headers[i]: (str(raw_row[i]).strip() if i < len(raw_row) else "")
               for i in range(len(headers))}
        rows.append(row)
    return headers, rows

def _import_row_error(row: int, e: Exception) -> dict:
    """Compress a row failure into a concise, field-level message the UI can
    render as a clean list (not a multi-line Pydantic dump)."""
    from pydantic import ValidationError
    if isinstance(e, ValidationError):
        fields = [str(err.get("loc", ("field",))[0]) for err in e.errors()]
        msgs = [f"{err.get('loc', ('field',))[0]!s}: {err.get('msg', 'invalid')}"
                for err in e.errors()]
        return {"row": row, "fields": fields, "error": "; ".join(msgs)}
    return {"row": row, "fields": [], "error": str(e)}

@router.post("/import/preview", dependencies=[import_limit()])
async def import_preview(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    file: UploadFile = File(...),
) -> dict:
    """Step 1 of the import wizard — detect columns + sample rows."""
    from app.core.limits import MAX_IMPORT_BYTES, MAX_IMPORT_ROWS, read_capped
    raw = await read_capped(file, MAX_IMPORT_BYTES)  # (#88) cap before reading
    # (audit/perf) openpyxl parsing of a multi-MB xlsx is CPU-bound — offload it
    # to a thread so it doesn't stall the event loop for every other request.
    headers, rows = await asyncio.to_thread(_parse_upload, file.filename or "", raw)
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_many_rows")
    return {"columns": headers, "sample": rows[:5], "total_rows": len(rows)}

def _apply_mapping(rows: list[dict], mapping: dict[str, str]) -> list[dict]:
    """Re-key each row from source columns to target fields via ``mapping``.

    ``mapping`` is ``{target_field: source_column}``. Empty values are dropped
    so model defaults apply.
    """
    out: list[dict] = []
    for r in rows:
        mapped = {
            target: r.get(source, "").strip()
            for target, source in mapping.items()
            if source and r.get(source, "").strip()
        }
        if mapped:
            out.append(mapped)
    return out

@router.post("/products/import", dependencies=[import_limit()])
async def import_products(
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    mapping: str = Form(...),
) -> dict:
    """Step 2 — bulk-create products from the uploaded file + column mapping.

    ``mapping`` is a JSON object ``{name_uz, sku, base_price, stock, ...}`` →
    source column name. Each created product with stock > 0 gets an opening
    ``in`` stock movement.
    """
    import json

    from app.services import inventory

    tenant, membership = ctx
    _require_owner_or_manager(membership)
    try:
        mapping_dict = json.loads(mapping)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_mapping") from None
    if "name_uz" not in mapping_dict:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name_uz_required")

    from app.core.limits import MAX_IMPORT_BYTES, MAX_IMPORT_ROWS, read_capped
    raw = await read_capped(file, MAX_IMPORT_BYTES)  # (#88)
    _, rows = await asyncio.to_thread(_parse_upload, file.filename or "", raw)
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_many_rows")
    mapped = _apply_mapping(rows, mapping_dict)

    # Pass 1 — validate + build every row. Nothing is written until we know the
    # whole file is clean, so a bad mapping never leaves a half-imported catalog.
    specs: list[tuple[dict, int | None]] = []
    errors: list[dict] = []
    for i, m in enumerate(mapped, start=1):
        try:
            stock_val = int(float(m["stock"])) if m.get("stock") else None
            kwargs = dict(
                tenant_id=tenant.id,
                name_uz=m["name_uz"],
                name_ru=m.get("name_ru"),
                sku=m.get("sku"),
                category=m.get("category"),
                brand=m.get("brand"),
                base_price=Decimal(m["base_price"]) if m.get("base_price") else Decimal("0"),
                cost_price=Decimal(m["cost_price"]) if m.get("cost_price") else None,
                stock=stock_val,
                min_stock_level=int(float(m["min_stock_level"])) if m.get("min_stock_level") else None,
            )
            specs.append((kwargs, stock_val))
        except (KeyError, ValueError, ArithmeticError) as e:
            errors.append(_import_row_error(i, e))

    if errors:
        return {"ok": False, "created": 0, "skipped": len(errors),
                "total": len(mapped), "errors": errors[:50]}

    # Pass 2 — all rows valid → create products + opening stock movements.
    for kwargs, stock_val in specs:
        product = Product(**kwargs)
        db.add(product)
        await db.flush()
        if stock_val and stock_val > 0:
            await inventory.record_movement(
                db, tenant_id=tenant.id, product_id=product.id,
                qty=stock_val, kind=StockMovementKind.in_,
                reason="import", by_user_id=user.id,
            )
    await db.commit()
    return {"ok": True, "created": len(specs), "skipped": 0, "total": len(mapped), "errors": []}

@router.post("/customers/import", dependencies=[import_limit()])
async def import_customers(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    mapping: str = Form(...),
) -> dict:
    """Bulk-create customers from an uploaded file + column mapping."""
    import json

    tenant, membership = ctx
    _require_owner_or_manager(membership)
    try:
        mapping_dict = json.loads(mapping)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_mapping") from None
    if "full_name" not in mapping_dict or "phone" not in mapping_dict:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name_phone_required")

    from app.core.limits import MAX_IMPORT_BYTES, MAX_IMPORT_ROWS, read_capped
    raw = await read_capped(file, MAX_IMPORT_BYTES)  # (#88)
    _, rows = await asyncio.to_thread(_parse_upload, file.filename or "", raw)
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too_many_rows")
    mapped = _apply_mapping(rows, mapping_dict)

    # Validate every row first; only commit if ALL rows are valid. A bad file
    # (e.g. wrong column mapping) imports nothing — the user fixes it and retries
    # rather than ending up with half a customer list of garbage.
    objs: list[Customer] = []
    errors: list[dict] = []
    for i, m in enumerate(mapped, start=1):
        try:
            payload = CustomerIn(
                full_name=m["full_name"],
                phone=m["phone"],
                secondary_phone=m.get("secondary_phone"),
                passport_series=m.get("passport_series"),
                passport_number=m.get("passport_number"),
                address=m.get("address"),
                employment_place=m.get("employment_place"),
            )
            data = payload.model_dump(exclude_unset=True)
            data["passport_id"] = _derive_passport_id(
                payload.passport_series, payload.passport_number, payload.passport_id,
            )
            objs.append(Customer(tenant_id=tenant.id, status=CustomerStatus.active, **data))
        except (KeyError, ValueError) as e:
            errors.append(_import_row_error(i, e))

    if errors:
        return {"ok": False, "created": 0, "skipped": len(errors),
                "total": len(mapped), "errors": errors[:50]}

    db.add_all(objs)
    await db.commit()
    return {"ok": True, "created": len(objs), "skipped": 0, "total": len(mapped), "errors": []}

# ── broadcasts ───────────────────────────────────────────────────────────────
# A tenant pushes a one-off message to its customers over the Telegram bot.
# Customers without a linked telegram_id are recorded as failed recipients so
# the owner can see reach. Sending is synchronous — fine at our per-tenant
# customer volumes; a queue can replace this later without changing the API.

def _broadcast_dict(b: Broadcast) -> dict:
    return {
        "id": str(b.id),
        "title": b.title,
        "body_uz": b.body_uz,
        "body_ru": b.body_ru,
        "media_url": b.media_url,
        "button_label": b.button_label,
        "button_url": b.button_url,
        "target": b.target.value if hasattr(b.target, "value") else str(b.target),
        "status": b.status.value if hasattr(b.status, "value") else str(b.status),
        "sent_count": b.sent_count,
        "failed_count": b.failed_count,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "finished_at": b.finished_at.isoformat() if b.finished_at else None,
    }

class BroadcastIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body_uz: str = Field(min_length=1, max_length=4000)
    body_ru: str | None = Field(default=None, max_length=4000)
    media_url: str | None = Field(default=None, max_length=500)
    button_label: str | None = Field(default=None, max_length=64)
    button_url: str | None = Field(default=None, max_length=500)
    target: BroadcastTarget = BroadcastTarget.all

@router.get("/broadcasts")
async def list_broadcasts(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: OffsetQ = 0,
) -> dict:
    tenant, _ = ctx
    rows = (await db.execute(
        select(Broadcast)
        .where(Broadcast.tenant_id == tenant.id)
        .order_by(Broadcast.created_at.desc(), Broadcast.id.desc())
        .offset(offset).limit(100)
    )).scalars().all()
    return {"items": [_broadcast_dict(b) for b in rows], "total": len(rows)}

@router.post("/broadcasts", status_code=status.HTTP_201_CREATED, dependencies=[broadcast_limit()])
async def create_broadcast(
    payload: BroadcastIn,
    background: BackgroundTasks,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Compose a broadcast and deliver it off the request path (#75).

    Persists only the Broadcast shell (status=sending) and hands delivery to a
    background task. A broadcast can fan out to thousands of Telegram sends;
    doing them inline would pin a DB connection and block the request for
    minutes. The UI polls GET /broadcasts for the final sent/failed counts.
    """
    tenant, membership = ctx
    _require_owner_or_manager(membership)

    b = Broadcast(
        tenant_id=tenant.id,
        title=payload.title.strip(),
        body_uz=payload.body_uz.strip(),
        body_ru=(payload.body_ru or None),
        media_url=payload.media_url or None,
        button_label=payload.button_label or None,
        button_url=payload.button_url or None,
        target=payload.target,
        status=BroadcastStatus.sending,
        created_by_user_id=membership.user_id,
        started_at=datetime.now(UTC),
    )
    db.add(b)
    await db.flush()  # assign b.id before audit + background scheduling reference it

    from app.services import audit
    await audit.record(
        db, action="broadcast.send", tenant_id=tenant.id,
        actor_user_id=membership.user_id, entity_type="broadcast", entity_id=str(b.id),
        after={"target": b.target.value},
    )
    await db.commit()
    await db.refresh(b)

    # Deliver after the response returns (own session, recipients committed at end).
    from app.services.notifications import deliver_broadcast_bg
    background.add_task(deliver_broadcast_bg, broadcast_id=b.id, tenant_id=tenant.id)

    return _broadcast_dict(b)

@router.delete("/broadcasts/{bid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_broadcast(
    bid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    b = (await db.execute(
        select(Broadcast).where(Broadcast.id == bid, Broadcast.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "broadcast_not_found")
    # Only terminal broadcasts are deletable — never one mid-send.
    if b.status not in (BroadcastStatus.failed, BroadcastStatus.draft, BroadcastStatus.sent):
        raise HTTPException(status.HTTP_409_CONFLICT, "broadcast_not_deletable")
    await db.delete(b)  # BroadcastRecipient rows cascade
    from app.services import audit
    await audit.record(
        db, action="broadcast.delete", tenant_id=tenant.id,
        actor_user_id=membership.user_id, entity_type="broadcast", entity_id=str(b.id),
    )
    await db.commit()
