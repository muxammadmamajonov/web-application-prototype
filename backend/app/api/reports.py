"""Owner analytics endpoints (Round 24).

``GET /tenant/reports/{aging,cashflow,staff-performance,products,ltv,margin}``

Each endpoint returns JSON by default, or a downloadable file when
``?format=csv|xlsx|pdf`` is passed. Computed results are cached in Redis for
5 minutes (keyed by tenant + report + params) — these aggregations are
read-heavy and don't need to be real-time for a dashboard.

Owner/manager only: sales staff don't see margin / commission data.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import date, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import report_limit
from app.core.redis import get_redis
from app.db import get_db
from app.models import Tenant, TenantUser
from app.schemas.common import LimitQ
from app.services import reports as svc
from app.services.notifications import send_tenant_document

from .tenant import _require_owner_or_manager, _tenant_today, resolve_tenant

log = logging.getLogger("muddato.reports")

router = APIRouter(prefix="/tenant/reports", tags=["reports"])

CACHE_TTL = 300  # 5 minutes
Fmt = Literal["json", "csv", "xlsx", "pdf"]


async def _cached(tenant_id: UUID, name: str, params: str,
                  compute: Callable[[], Any]) -> dict:
    """Return a JSON-encodable dict, served from Redis when warm."""
    rds = get_redis()
    key = f"report:{tenant_id}:{name}:{params}"
    try:
        hit = await rds.get(key)
        if hit:
            return orjson.loads(hit)
    except Exception:
        # Redis fault or an undecodable cached blob — fall through to a fresh
        # compute (the pre-existing behaviour). Debug, matching cache.py: a Redis
        # outage would otherwise log once per report request.
        log.debug("report.cache.read_failed report=%s tenant=%s", name, tenant_id, exc_info=True)
    data = jsonable_encoder(await compute())
    with suppress(Exception):
        await rds.set(key, orjson.dumps(data), ex=CACHE_TTL)
    return data


# ── export helpers ───────────────────────────────────────────────────────────

# (SEC-010) A spreadsheet evaluates a cell whose first effective character is one
# of these, so an operator opening an export would execute whatever a customer
# name or note happened to contain. One predicate serves BOTH writers below.
_FORMULA_LEAD = ("=", "+", "-", "@")
# A leading tab/CR/LF is itself enough to make Excel re-parse the cell, and plain
# leading spaces do not stop evaluation — so neither may be used to slip past.
_CONTROL_LEAD = ("\t", "\r", "\n")


def _safe_cell(value: object) -> object:
    """Prefix a single apostrophe when a STRING would be read as a formula.

    Non-strings pass through untouched so numbers stay numbers — including
    negatives, which begin with ``-`` but are not user text. Idempotent: a value
    already carrying the apostrophe is returned as-is rather than stacking more.
    """
    if not isinstance(value, str) or not value:
        return value
    if value.startswith("'"):
        return value
    # `lstrip()` (all whitespace, not just spaces) so a mixed run such as
    # " \t=1+1" cannot hide the marker behind a tab.
    if value.startswith(_CONTROL_LEAD) or value.lstrip().startswith(_FORMULA_LEAD):
        return "'" + value
    return value


def _csv_bytes(columns: list[tuple[str, str]], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in columns])   # static headers: never user data
    for r in rows:
        w.writerow([_safe_cell(r.get(k, "")) for k, _ in columns])
    return buf.getvalue().encode("utf-8-sig")  # BOM → Excel reads UTF-8


def _xlsx_bytes(title: str, columns: list[tuple[str, str]], rows: list[dict]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31] or "Report"
    ws.append([label for _, label in columns])   # static headers: never user data
    for r in rows:
        ws.append([_safe_cell(r.get(k, "")) for k, _ in columns])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _pdf_bytes(title: str, columns: list[tuple[str, str]], rows: list[dict]) -> bytes:
    from html import escape

    from weasyprint import HTML

    head = "".join(f"<th>{escape(label)}</th>" for _, label in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(str(r.get(k, '')))}</td>" for k, _ in columns) + "</tr>"
        for r in rows
    )
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
      body {{ font-family: 'DejaVu Sans', sans-serif; font-size: 11px; color: #1c1f29; }}
      h1 {{ font-size: 16px; margin: 0 0 12px; }}
      table {{ border-collapse: collapse; width: 100%; }}
      th, td {{ border: 1px solid #d9d4c7; padding: 5px 8px; text-align: left; }}
      th {{ background: #f3efe6; }}
      .meta {{ color: #8a8676; font-size: 10px; margin-bottom: 10px; }}
    </style></head><body>
      <h1>{escape(title)}</h1>
      <div class="meta">{date.today().isoformat()} · Muddato</div>
      <table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
    </body></html>"""
    return HTML(string=html).write_pdf()


#: (MUDD-121) Every report row below carries BOTH a legacy scalar — the
#: primary currency's share — AND a `*_by_currency` map beside it (MUDD-114).
#: The scalar is all an export column list used to read, so a seller who sold
#: a USD contract had their commission narrowed to the primary currency for
#: the SCREEN and then silently dropped altogether from the CSV/XLSX/PDF/
#: Telegram export, which shares this same column-building code.
#:
#: This turns each `*_by_currency` map into one row per currency actually
#: present, rather than adding a currency column per known code: the set of
#: currencies a tenant holds is open-ended (`SUPPORTED_CURRENCIES` may grow),
#: and a fixed wide layout would need a schema change every time it did.
#: Non-money fields (name, count, qty, …) repeat on every currency row for
#: that entity — they are not the figure that was going missing.
def _explode_by_currency(rows: list[dict], amount_fields: dict[str, str]) -> list[dict]:
    """One row per currency present, for export.

    ``amount_fields`` maps an output column key (e.g. ``"commission"``) to the
    row's already-computed by-currency field name (e.g.
    ``"commission_by_currency"``). A row carrying no currency at all (every
    map empty) is kept once, unchanged, rather than dropped.
    """
    out: list[dict] = []
    for row in rows:
        currencies: list[str] = []
        for field in amount_fields.values():
            for code in row.get(field) or {}:
                if code not in currencies:
                    currencies.append(code)
        if not currencies:
            out.append(dict(row))
            continue
        for code in currencies:
            new_row = dict(row)
            new_row["currency"] = code
            for col, field in amount_fields.items():
                # `data` has already passed through `jsonable_encoder` (Decimal
                # → float) by the time an endpoint calls this, so the fallback
                # matches — not the Decimal the service layer computed with.
                new_row[col] = (row.get(field) or {}).get(code, 0)
            out.append(new_row)
    return out


def _report_bytes(fmt: Fmt, name: str, title: str,
                  columns: list[tuple[str, str]], rows: list[dict]) -> tuple[bytes, str, str]:
    """Build the export file → (data, filename, media-type)."""
    if fmt == "csv":
        data, media, ext = _csv_bytes(columns, rows), "text/csv", "csv"
    elif fmt == "xlsx":
        data = _xlsx_bytes(title, columns, rows)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ext = "xlsx"
    else:  # pdf
        data, media, ext = _pdf_bytes(title, columns, rows), "application/pdf", "pdf"
    return data, f"{name}-{date.today().isoformat()}.{ext}", media


def _export(fmt: Fmt, name: str, title: str,
            columns: list[tuple[str, str]], rows: list[dict]) -> StreamingResponse:
    data, fname, media = _report_bytes(fmt, name, title, columns, rows)
    return StreamingResponse(
        io.BytesIO(data), media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


async def _respond(deliver: str, db: AsyncSession, tenant_id: UUID, fmt: Fmt, name: str,
                   title: str, columns: list[tuple[str, str]], rows: list[dict]):
    """(#206) Stream the file as a download (default) OR deliver it to the tenant
    owner's Telegram (deliver='telegram')."""
    if deliver == "telegram":
        data, fname, _media = await asyncio.to_thread(_report_bytes, fmt, name, title, columns, rows)  # (#122) render off loop
        n = await send_tenant_document(
            db, tenant_id=tenant_id, data=data, filename=fname, caption=f"{title} · {fname}",
        )
        if not n:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "telegram_send_failed — botni /start qiling, keyin qayta urinib ko'ring",
            )
        return {"sent": n, "filename": fname}
    return await asyncio.to_thread(_export, fmt, name, title, columns, rows)  # (#122) render off loop


# ── endpoints ────────────────────────────────────────────────────────────────

@router.get("/aging", dependencies=[report_limit()])
async def report_aging(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    format: Fmt = "json",
    deliver: str = "download",
):
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    data = await _cached(tenant.id, "aging", "v1",
                         lambda: svc.aging(db, tenant.id))
    if format == "json":
        return data
    b = data["buckets"]
    labels = {"b0_30": "0–30", "b31_60": "31–60", "b61_90": "61–90", "b90_plus": "90+"}
    # The legacy scalar stays on the row: an EMPTY bucket has an empty
    # by-currency map, so it takes `_explode_by_currency`'s keep-once path and
    # prints whatever the row already carries. Without it that cell would go
    # blank where it used to read 0 — the same silent-blank shape this lane
    # exists to close. Every other report below keeps its scalar for free.
    rows = [{"bucket": labels[k], "count": b[k]["count"],
             "outstanding": b[k]["outstanding"],
             "outstanding_by_currency": b[k].get("by_currency") or {}}
            for k in ("b0_30", "b31_60", "b61_90", "b90_plus")]
    rows = _explode_by_currency(rows, {"outstanding": "outstanding_by_currency"})
    return await _respond(deliver, db, tenant.id, format, "aging", "Aging report",
                   [("bucket", "Kun"), ("count", "Soni"), ("currency", "Valyuta"),
                    ("outstanding", "Qarz")], rows)


@router.get("/cashflow", dependencies=[report_limit()])
async def report_cashflow(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    granularity: Literal["day", "week", "month"] = "day",
    format: Fmt = "json",
    deliver: str = "download",
):
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    # (MUDD-140) The default window must anchor on the tenant's own business
    # day, not the container's (UTC) — otherwise 00:00-05:00 Asia/Tashkent
    # still resolves to "yesterday" and drops today's payments from a
    # no-args request, even though `svc.cashflow` itself buckets correctly.
    to = date_to or _tenant_today(tenant)
    frm = date_from or (to - timedelta(days=30))
    # (#810) A reversed range silently returned an empty report; reject it.
    if frm > to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "from_after_to")
    params = f"{frm}:{to}:{granularity}"
    data = await _cached(tenant.id, "cashflow", params,
                         lambda: svc.cashflow(db, tenant.id, date_from=frm, date_to=to,
                                              granularity=granularity))
    if format == "json":
        return data
    rows = _explode_by_currency(data["series"], {"amount": "by_currency"})
    return await _respond(deliver, db, tenant.id, format, "cashflow", "Cashflow report",
                   [("period", "Sana"), ("count", "To'lovlar"), ("currency", "Valyuta"),
                    ("amount", "Summa")],
                   rows)


@router.get("/staff-performance", dependencies=[report_limit()])
async def report_staff(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    format: Fmt = "json",
    deliver: str = "download",
):
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    data = await _cached(tenant.id, "staff", "v1",
                         lambda: svc.staff_performance(db, tenant.id))
    if format == "json":
        return data
    rows = _explode_by_currency(
        data["staff"], {"volume": "volume_by_currency", "commission": "commission_by_currency"})
    return await _respond(deliver, db, tenant.id, format, "staff-performance", "Sotuvchilar samaradorligi",
                   [("name", "Sotuvchi"), ("sales_count", "Sotuvlar"), ("currency", "Valyuta"),
                    ("volume", "Hajm"), ("commission_rate_pct", "Stavka %"),
                    ("commission", "Komissiya")],
                   rows)


@router.get("/products", dependencies=[report_limit()])
async def report_products(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    format: Fmt = "json",
    deliver: str = "download",
):
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    data = await _cached(tenant.id, "products", "v1",
                         lambda: svc.products(db, tenant.id))
    if format == "json":
        return data
    rows = _explode_by_currency(data["rows"], {"revenue": "revenue_by_currency"})
    return await _respond(deliver, db, tenant.id, format, "products", "Mahsulotlar bo'yicha",
                   [("brand", "Brend"), ("category", "Kategoriya"), ("qty", "Soni"),
                    ("currency", "Valyuta"), ("revenue", "Tushum")],
                   rows)


@router.get("/ltv", dependencies=[report_limit()])
async def report_ltv(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: LimitQ = 20,
    format: Fmt = "json",
    deliver: str = "download",
):
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    data = await _cached(tenant.id, "ltv", f"limit={limit}",
                         lambda: svc.ltv(db, tenant.id, limit=limit))
    if format == "json":
        return data
    rows = _explode_by_currency(
        data["customers"],
        {"lifetime_value": "lifetime_value_by_currency", "paid": "paid_by_currency"})
    return await _respond(deliver, db, tenant.id, format, "ltv", "Mijoz LTV",
                   [("name", "Mijoz"), ("phone", "Telefon"), ("contracts", "Shartnomalar"),
                    ("currency", "Valyuta"), ("lifetime_value", "LTV"), ("paid", "To'langan")],
                   rows)


@router.get("/margin", dependencies=[report_limit()])
async def report_margin(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    format: Fmt = "json",
    deliver: str = "download",
):
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    data = await _cached(tenant.id, "margin", "v1",
                         lambda: svc.margin(db, tenant.id))
    if format == "json":
        return data
    # A single tenant-wide aggregate, not a list of entities — wrapped in a
    # one-element list so it goes through the same per-currency explosion as
    # every other report, rather than a bespoke branch.
    rows = _explode_by_currency([data], {
        "revenue": "revenue_by_currency",
        "cost_known": "cost_known_by_currency",
        "gross_profit": "gross_profit_by_currency",
        "margin_pct": "margin_pct_by_currency",
    })
    return await _respond(deliver, db, tenant.id, format, "margin", "Margina (foyda)",
                   [("currency", "Valyuta"), ("revenue", "Tushum"),
                    ("cost_known", "Tan narxi"), ("gross_profit", "Yalpi foyda"),
                    ("margin_pct", "Marja %"), ("qty_with_cost", "Tannarxi ma'lum"),
                    ("qty_total", "Jami soni")],
                   rows)
