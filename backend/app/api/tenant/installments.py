"""Tenant back-office: the merchandise a shop sells and the instalment contracts
that sell it."""
from __future__ import annotations

import logging
from dataclasses import replace as dataclass_replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Integer, and_, case, cast, func, or_, select, text, tuple_
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalog_identifiers import find_identifier_field, validate_catalog_identifier
from app.core.category_identifiers import (
    CategoryIdentifierError,
    normalize_identifier_value,
    parse_identifier_schema,
    validate_category_identifiers,
)
from app.core.identifiers import MAX_IDENTIFIER_LENGTH, validate_identifier
from app.core.ratelimit import search_limit_if_query, upload_limit
from app.core.security import current_user
from app.db import get_db
from app.models import (
    CatalogCategory,
    CatalogModel,
    Customer,
    Guarantor,
    Installment,
    InstallmentGuarantor,
    InstallmentItem,
    InstallmentItemIdentifier,
    Payment,
    Product,
    Tenant,
    TenantUser,
    User,
)
from app.models.enums import (
    CustomerStatus,
    InstallmentStatus,
    StockMovementKind,
)
from app.schemas.common import LimitQ, OffsetQ, SearchQ

from ._shared import (
    _MAX_ITEMS,
    _MAX_MONEY,
    _MAX_QTY,
    _MAX_UNIT_PRICE,
    _require_owner_or_manager,
    _tenant_today,
    _tenant_tz_name,
    resolve_tenant,
)

router = APIRouter()

# ── products ───────────────────────────────────────────────────────────────

class ProductIn(BaseModel):
    name_uz:        str = Field(min_length=1, max_length=120)
    name_ru:        str | None = Field(default=None, max_length=120)
    sku:            str | None = Field(default=None, max_length=64)
    category:       str | None = Field(default=None, max_length=64)
    brand:          str | None = Field(default=None, max_length=64)
    catalog_model_id: UUID | None = None
    base_price:     Decimal = Field(ge=0, le=_MAX_MONEY)
    cost_price:     Decimal | None = Field(default=None, ge=0, le=_MAX_MONEY)
    stock:          int | None = Field(default=None, ge=0)
    description_uz: str | None = Field(default=None, max_length=1500)
    description_ru: str | None = Field(default=None, max_length=1500)
    attributes:     dict | None = None
    photo_urls:     list[str] | None = None
    is_active:      bool = True

class ProductPatch(BaseModel):
    name_uz:        str | None = None
    name_ru:        str | None = None
    sku:            str | None = None
    category:       str | None = None
    brand:          str | None = None
    catalog_model_id: UUID | None = None
    base_price:     Decimal | None = Field(default=None, ge=0, le=_MAX_MONEY)
    cost_price:     Decimal | None = Field(default=None, ge=0, le=_MAX_MONEY)
    stock:          int | None = Field(default=None, ge=0)
    description_uz: str | None = None
    description_ru: str | None = None
    attributes:     dict | None = None
    photo_urls:     list[str] | None = None
    is_active:      bool | None = None

def _product_dict(p: Product) -> dict:
    return {
        "id":              str(p.id),
        "sku":             p.sku,
        "name_uz":         p.name_uz,
        "name_ru":         p.name_ru,
        "category":        p.category,
        "brand":           p.brand,
        "catalog_model_id": str(p.catalog_model_id) if p.catalog_model_id else None,
        "base_price":      int(p.base_price or 0),
        "cost_price":      int(p.cost_price or 0) if p.cost_price else None,
        "stock":           p.stock,
        "min_stock_level": p.min_stock_level,
        "attributes":      p.attributes or {},
        "photo_urls":      p.photo_urls or [],
        "is_active":       p.is_active,
        "created_at":      p.created_at.isoformat(),
    }

@router.get("/products")
async def list_products(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str | None = None,
    limit: LimitQ = 50, offset: OffsetQ = 0,
) -> dict:
    tenant, _ = ctx
    base = select(Product).where(Product.tenant_id == tenant.id)
    if q:
        like = f"%{q.lower()}%"
        base = base.where(
            func.lower(Product.name_uz).like(like)
            | func.lower(Product.name_ru).like(like)
            | func.lower(Product.sku).like(like)
        )
    rows = (await db.execute(
        base.order_by(Product.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all()
    total = (await db.execute(
        select(func.count(Product.id)).where(Product.tenant_id == tenant.id)
    )).scalar_one()
    return {"items": [_product_dict(p) for p in rows], "total": total}

@router.get("/products/{pid}")
async def get_product(
    pid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, _ = ctx
    p = (await db.execute(
        select(Product).where(Product.id == pid, Product.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product_not_found")
    return _product_dict(p)

@router.post("/products", status_code=status.HTTP_201_CREATED)
async def create_product(
    body: ProductIn,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    # If a catalog_model_id is provided, hydrate brand/category from it for free.
    data = body.model_dump(exclude_unset=True)
    if body.catalog_model_id:
        await _hydrate_from_catalog(db, body.catalog_model_id, data, tenant_id=tenant.id)
    product = Product(tenant_id=tenant.id, **data)
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return _product_dict(product)

async def _hydrate_from_catalog(db: AsyncSession, model_id: UUID, data: dict, *, tenant_id: UUID) -> None:
    """Fill in category/brand/name from the catalog entry when picked.

    (F-25) Scoped to global catalog rows (tenant_id NULL) + the caller's own
    proposed models, so a tenant can't hydrate from another tenant's proposed
    model by guessing its UUID.
    """
    m = (await db.execute(
        select(CatalogModel).where(
            CatalogModel.id == model_id,
            or_(CatalogModel.tenant_id.is_(None), CatalogModel.tenant_id == tenant_id),
        )
    )).scalar_one_or_none()
    if not m:
        return
    brand = await m.awaitable_attrs.brand
    category = await brand.awaitable_attrs.category
    data.setdefault("brand", brand.name)
    data.setdefault("category", category.slug)
    data.setdefault("name_uz", m.name)
    data.setdefault("name_ru", m.name)

@router.patch("/products/{pid}")
async def update_product(
    pid: UUID,
    body: ProductPatch,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    p = (await db.execute(
        select(Product).where(Product.id == pid, Product.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product_not_found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    await db.commit()
    await db.refresh(p)
    return _product_dict(p)

@router.post("/upload-image", dependencies=[upload_limit()])
async def tenant_upload_image(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    file: Annotated[UploadFile, File()],
) -> dict:
    """Any tenant member — store a product photo on archive.org, return its URL."""
    from app.core.limits import MAX_UPLOAD_BYTES, read_capped
    from app.services.media import upload_image as _store_image
    data = await read_capped(file, MAX_UPLOAD_BYTES)  # (#88) cap before buffering
    url = await _store_image(data, file.content_type)
    return {"url": url}

@router.post("/upload-kyc", dependencies=[upload_limit()])
async def tenant_upload_kyc(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File()],
) -> dict:
    """Any tenant member — store a KYC photo (passport / national-ID / employment
    / guarantor) PRIVATELY and return its opaque key (#113).

    Unlike /upload-image (public archive.org, fine for product photos), KYC bytes
    never become world-readable — they go to a private KycBlob (Postgres or a
    private bucket) served only via the tenant-bound /kyc-media proxy. The image
    is sanitized (EXIF/GPS stripped + sniffed) on the way in (#114). Persist the
    returned ``key`` in the customer/guarantor photo list."""
    tenant, _ = ctx
    from app.core.limits import MAX_UPLOAD_BYTES, read_capped
    from app.services.media import store_kyc_image
    data = await read_capped(file, MAX_UPLOAD_BYTES)  # (#88) cap before buffering
    key = await store_kyc_image(db, data, file.content_type, tenant_id=tenant.id)
    await db.commit()
    return {"key": key}

@router.delete("/products/{pid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    pid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    p = (await db.execute(
        select(Product).where(Product.id == pid, Product.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product_not_found")
    await db.delete(p)
    await db.commit()

# ── installments ───────────────────────────────────────────────────────────

class InstallmentItemIn(BaseModel):
    #: Every field is listed explicitly and unknown keys are refused, so a new
    #: column can never be set by a client that simply guesses its name.
    model_config = ConfigDict(extra="forbid")

    product_id:            UUID | None = None
    catalog_model_id:      UUID | None = None
    product_name_snapshot: str = Field(min_length=1, max_length=160)
    variant_label:         str | None = Field(default=None, max_length=120)
    serial_number:         str | None = Field(default=None, max_length=MAX_IDENTIFIER_LENGTH)
    #: What `serial_number` is. `LEGACY_UNVERIFIED` is a migration outcome and
    #: is deliberately absent from what a client may send.
    identifier_type:       Literal["IMEI", "SERIAL_OR_CONTRACT"] | None = None
    #: (MUDD-83) A `catalog_categories.slug`. Optional: when the client omits
    #: it the server derives it from `catalog_model_id`, then `product_id` (see
    #: `_resolve_item_categories`), so existing clients keep working and get
    #: categorised rows for free. When present it WINS — the picker is the one
    #: place a human states what was sold, and a stale product row must not
    #: overrule them.
    category:              str | None = Field(default=None, max_length=64)
    #: (MUDD-85) Category-schema-driven fields (IMEI 1/2, VIN, …), keyed by
    #: `catalog_categories.identifier_schema[].key`. Validated against the
    #: resolved category's schema in `_validate_item_identifiers` — not here,
    #: since the category (and therefore the applicable schema) is only known
    #: once `_resolve_item_categories` has run for the whole batch.
    identifiers:           dict[str, str] = Field(default_factory=dict)
    qty:                   int = Field(default=1, gt=0, le=_MAX_QTY)        # DB CHECK qty > 0
    unit_price:            Decimal = Field(ge=0, le=_MAX_UNIT_PRICE)        # DB CHECK unit_price >= 0

    @model_validator(mode="after")
    def _check_identifier(self) -> InstallmentItemIn:
        """Value and kind are validated together — neither means anything alone.

        The normalised value is written back, so what is stored is what the
        duplicate check compares: a scanned `35-209900-176148-1` and a typed
        `352099001761481` are the same device.
        """
        value, error = validate_identifier(self.identifier_type, self.serial_number)
        if error is not None:
            raise ValueError(error)
        object.__setattr__(self, "serial_number", value)
        if value is None:
            object.__setattr__(self, "identifier_type", None)
        return self

async def _resolve_item_categories(
    db: AsyncSession,
    items: list[InstallmentItemIn],
    products_by_id: dict[UUID, Product],
) -> list[str | None]:
    """Decide each line's category slug once, for both write paths.

    Precedence, most authoritative first:

    1. What the client sent. The category picker is the only place a person
       states what they are selling, and a tenant's own product row can be
       stale or wrong; their answer wins over anything we could infer.
    2. `catalog_model_id -> catalog_models.category_id`. Deliberately NOT via
       `catalog_brands`: a brand spans several categories now
       (`catalog_brands.category_ids`), so the brand hop would file a phone
       brand's laptop under `smartfonlar`. This is also why this function does
       not reuse `products._hydrate_from_catalog`, which takes exactly that hop
       — and would raise `AttributeError` if it ever ran, since `CatalogBrand`
       has no `category` relationship.
    3. `product_id -> products.category`, for lines that name a stocked item.
    4. NULL.

    NULL is a real answer, not a failure: a free-text line with no product and
    no catalog model genuinely has no category, and inventing one would put
    fiction into a revenue breakdown.

    Every slug that survives — sent or derived — is checked against an
    EXISTING `catalog_categories` row (retired or not — see the note below on
    why this is existence, not `is_active`). `products.category` is free text
    and can hold a legacy value that matches no row at all; letting one through
    would fragment the breakdown into buckets the category filter can never
    select, and nothing downstream would notice. Two batched queries
    regardless of item count.
    """
    model_ids = {it.catalog_model_id for it in items if it.catalog_model_id is not None}
    category_by_model: dict[UUID, str] = {}
    if model_ids:
        category_by_model = {
            model_id: slug
            for model_id, slug in (await db.execute(
                select(CatalogModel.id, CatalogCategory.slug)
                .join(CatalogCategory, CatalogCategory.id == CatalogModel.category_id)
                .where(CatalogModel.id.in_(model_ids))
            )).all()
        }

    candidates: list[str | None] = []
    for it in items:
        if it.category:
            candidates.append(it.category.strip() or None)
            continue
        if it.catalog_model_id is not None and it.catalog_model_id in category_by_model:
            candidates.append(category_by_model[it.catalog_model_id])
            continue
        product = products_by_id.get(it.product_id) if it.product_id else None
        candidates.append((product.category or "").strip() or None if product else None)

    wanted = {slug for slug in candidates if slug}
    if not wanted:
        return candidates
    # (MUDD-92 H1/M1) EXISTENCE, not `is_active` — a saved contract's snapshot
    # round-trips through PUT as this same `it.category` field (the edit form
    # rehydrates every line from what is already stored, see
    # `InstallmentModal.tsx`'s prefill effect), so a category retired AFTER the
    # sale would otherwise make that line's own unchanged value look like a
    # client invention and 400 an edit that touches nothing about the category.
    # A slug still has to be a REAL `catalog_categories` row — `is_active` only
    # controls whether it is offered for a NEW pick, not whether one already on
    # the books is valid. This is also the exact predicate
    # `rj0itemcategory_installment_item_category.py`'s backfill uses; the two
    # must agree; see that migration's docstring.
    existing = set((await db.execute(
        select(CatalogCategory.slug).where(CatalogCategory.slug.in_(wanted))
    )).scalars().all())

    # A slug the CLIENT chose and we do not recognise AT ALL is a bug in the
    # caller, and silently storing it would let the UI show a category the
    # filter can never match. Say so. A slug we DERIVED from a stale
    # `products.category` is our own legacy data, and refusing the sale over it
    # would punish the tenant for a row they cannot see — drop it to NULL
    # instead.
    sent = {it.category.strip() for it in items if it.category and it.category.strip()}
    unknown_sent = sorted(sent - existing)
    if unknown_sent:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown_category:{unknown_sent[0]}",
        )
    return [slug if slug in existing else None for slug in candidates]


#: One already-stored line of a contract that is about to be replaced:
#: (category slug, product name snapshot, {identifier key: stored value}).
_StoredLine = tuple[str | None, str, dict[str, str]]


async def _load_stored_item_identifiers(
    db: AsyncSession, installment_id: UUID,
) -> list[_StoredLine]:
    """(MUDD-123) Snapshot what a contract's lines already carry, before the edit
    deletes them.

    MUST be called before `replace_installment`'s `DELETE FROM installment_items`
    — that delete CASCADEs the identifier rows away, and after it the database
    no longer knows what this contract had. Two queries regardless of line count.

    Ordered by `created_at, id` so the greedy match in `_grandfathered_identifiers`
    is deterministic when a contract carries two identical lines.
    """
    items = (await db.execute(
        select(InstallmentItem.id, InstallmentItem.category,
               InstallmentItem.product_name_snapshot)
        .where(InstallmentItem.installment_id == installment_id)
        .order_by(InstallmentItem.created_at, InstallmentItem.id)
    )).all()
    if not items:
        return []
    stored: dict[UUID, dict[str, str]] = {}
    for item_id, key, value in (await db.execute(
        select(InstallmentItemIdentifier.item_id, InstallmentItemIdentifier.key,
               InstallmentItemIdentifier.value)
        .where(InstallmentItemIdentifier.item_id.in_([i.id for i in items]))
    )).all():
        stored.setdefault(item_id, {})[key] = value
    return [(i.category, i.product_name_snapshot, stored.get(i.id, {})) for i in items]


def _grandfathered_identifiers(
    stored_lines: list[_StoredLine],
    items: list[InstallmentItemIn],
    item_categories: list[str | None],
) -> list[dict[str, str] | None]:
    """(MUDD-123) Pair each submitted line with the stored line it is replacing.

    `None` means "no stored counterpart" — a line being ADDED by this edit, which
    gets no relief at all and must satisfy today's schema in full. A dict (empty
    dicts included) means "this line already exists on the books, and this is what
    it carries"; `_validate_item_identifiers` uses it to decide which of today's
    rules that line was ever given a chance to satisfy.

    Lines are paired on `(category, product_name_snapshot)`, greedily and in
    stored order, each stored line consumed at most once. The PUT body carries no
    line ids — `replace_installment` deletes every row and writes new UUIDs — so
    there is no stable identity to match on; this pair is the strongest signal
    available, and the edit form rehydrates both halves of it verbatim from the
    GET response.

    A mismatch is deliberately biased toward "unmatched": failing to pair costs
    an edit that the operator can still complete by supplying the value, while
    pairing wrongly would let a genuinely new line skip a required identifier.
    """
    pool: dict[tuple[str | None, str], list[dict[str, str]]] = {}
    for slug, name, identifiers in stored_lines:
        pool.setdefault((slug, name), []).append(identifiers)

    matched: list[dict[str, str] | None] = []
    for item, slug in zip(items, item_categories, strict=True):
        bucket = pool.get((slug, item.product_name_snapshot))
        matched.append(bucket.pop(0) if bucket else None)
    return matched


async def _validate_item_identifiers(
    db: AsyncSession,
    items: list[InstallmentItemIn],
    item_categories: list[str | None],
    tenant_id: UUID,
    grandfathered: list[dict[str, str] | None] | None = None,
) -> list[dict[str, str]]:
    """(MUDD-85) Validate + normalize each line's `identifiers` against its
    resolved category's `identifier_schema`, then refuse any value already
    recorded for this tenant under the same key — on ANY contract, not just
    this one.

    An item with no resolved category has no schema, so an empty
    `identifiers` dict is the only thing it can validly submit
    (`validate_category_identifiers` rejects every key against an empty
    schema, the same as `_resolve_item_categories` rejects an unrecognised
    category slug).

    Runs on BOTH write paths (create and full edit). An edit deletes and
    recreates every item (see `replace_installment`), so it re-enters this same
    check — which is why `grandfathered` exists.

    (MUDD-123) `grandfathered[i]` is what the stored line this submission is
    replacing already carries, or `None` for a line the edit is ADDING (see
    `_grandfathered_identifiers`). It relaxes today's schema for that line, in
    exactly two ways and no further:

      * a REQUIRED field the stored line never captured may stay empty. It
        cannot be filled in honestly: `r96catidschema` made 114 categories
        require an identifier retroactively with no backfill, so every contract
        written before it — and every one written before `rk0catident` created
        the table at all — has none. Demanding an IMEI to correct a typo in a
        customer's name demands a number the shop cannot look up: the phone
        left with the customer months ago. Supplying a NEW value for that field
        is still fully validated; only its absence is forgiven.
      * a value RESUBMITTED UNCHANGED passes verbatim, `validator` included. A
        stored IMEI that fails today's Luhn check is wrong, but it is what was
        recorded, and refusing to let the operator save an unrelated field until
        they "fix" it just invites an invented number. Change that value and the
        new one faces the current rules in full.

    Nothing here reaches the create path — `grandfathered` is `None` there, so a
    NEW contract's required identifiers stay required, which is the whole point
    of MUDD-113. The rejected alternative was backfilling the missing values by
    migration; see this endpoint's PR and `docs/CATALOG_IDENTIFIERS.md`.

    The DB `uq_item_identifier_tenant_key_value` constraint is the race-safe
    backstop for the cross-tenant-row check below — two concurrent requests
    can both pass this SELECT before either commits; the constraint is what
    actually decides the outcome (see the `IntegrityError` handling at each
    call site).

    (MUDD-113) `validate_category_identifiers` only enforces what
    `IdentifierField` carries — required/max_len/digits-only — it drops each
    entry's `validator` key. A field declaring `validator: "imei"`/`"vin"`/etc
    is re-checked here against the raw schema via `find_identifier_field` +
    `validate_catalog_identifier`, so a bad Luhn digit or an ISO-3779-excluded
    VIN character is rejected instead of merely stored as an oddly-shaped
    string. `normalized[key]` is replaced with the validator's own normalized
    form (e.g. an upper-cased VIN) so the persisted value is canonical.

    `validate_catalog_identifier` can also return `(None, None)`: its
    normalizer strips a wider set of invisible characters (ZWSP, soft hyphen,
    BOM) than `validate_category_identifiers`' does, so a value that looked
    non-blank at the first pass (e.g. one lone ZWSP) can collapse to nothing
    here. That is not an error on an optional field — it means "nothing was
    really captured" — so the key is dropped instead of persisted or treated
    as a bug.
    """
    wanted_slugs = {slug for slug in item_categories if slug}
    schema_by_slug: dict[str, list] = {}
    raw_schema_by_slug: dict[str, list] = {}
    if wanted_slugs:
        rows = (await db.execute(
            select(CatalogCategory.slug, CatalogCategory.identifier_schema)
            .where(CatalogCategory.slug.in_(wanted_slugs))
        )).all()
        schema_by_slug = {slug: parse_identifier_schema(raw) for slug, raw in rows}
        raw_schema_by_slug = {slug: (raw if isinstance(raw, list) else []) for slug, raw in rows}

    results: list[dict[str, str]] = []
    seen_in_payload: set[tuple[str, str]] = set()
    stored_lines = grandfathered if grandfathered is not None else [None] * len(items)
    for it, slug, stored in zip(items, item_categories, stored_lines, strict=True):
        schema = schema_by_slug.get(slug, []) if slug else []
        raw_schema = raw_schema_by_slug.get(slug, []) if slug else []

        # (MUDD-123) Split the submission into "same as what is already on the
        # books" and "new or changed". Only the second half meets today's rules.
        submitted = dict(it.identifiers)
        retained: dict[str, str] = {}
        if stored is not None:
            declared = {f.key for f in schema}
            unchanged = {
                key: value for key, value in stored.items()
                if key in submitted and normalize_identifier_value(submitted[key]) == value
            }
            for key in unchanged:
                del submitted[key]
            # A key the category no longer declares is dropped, not rejected and
            # not re-persisted: r96catidschema REPLACED r55's schemas wholesale,
            # so a line saved between the two carries keys that no longer exist.
            # The edit form echoes them straight back from GET, and refusing them
            # would block the same edits the required-field relief just unblocked.
            retained = {k: v for k, v in unchanged.items() if k in declared}
            schema = [
                dataclass_replace(f, required=False)
                if f.required and (f.key in retained or f.key not in stored) else f
                for f in schema
            ]

        try:
            normalized = validate_category_identifiers(schema, submitted)
        except CategoryIdentifierError as exc:
            key, code = next(iter(sorted(exc.errors.items())))
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{code}:{key}") from None

        for key in list(normalized):
            field = find_identifier_field(raw_schema, key)
            if field is None:
                continue
            checked_value, err = validate_catalog_identifier(field, normalized[key])
            if err:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{err}:{key}")
            if checked_value is None:
                del normalized[key]
                continue
            normalized[key] = checked_value

        # (MUDD-123) The grandfathered values rejoin here, AFTER the validator
        # pass they are exempt from but BEFORE both duplicate checks — a value
        # that is old is still not allowed to be a second copy of another line's.
        normalized.update(retained)

        for key, value in normalized.items():
            pair = (key, value)
            if pair in seen_in_payload:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"category_identifier_duplicate:{key}",
                )
            seen_in_payload.add(pair)
        results.append(normalized)

    all_pairs = {pair for r in results for pair in r.items()}
    if all_pairs:
        existing_pairs = set((await db.execute(
            select(InstallmentItemIdentifier.key, InstallmentItemIdentifier.value)
            .where(
                InstallmentItemIdentifier.tenant_id == tenant_id,
                tuple_(InstallmentItemIdentifier.key, InstallmentItemIdentifier.value)
                .in_(all_pairs),
            )
        )).all())
        if existing_pairs:
            dup_key = sorted(existing_pairs)[0][0]
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"category_identifier_duplicate:{dup_key}",
            )
    return results


class InstallmentIn(BaseModel):
    customer_id:   UUID
    # Upper bound matches TenantSettings.default_max_months (le=120) and the
    # reschedule ceiling — a tenant configured for >60 months must not 422 here.
    months:        int = Field(ge=1, le=120)
    down_payment:  Decimal = Field(default=Decimal("0"), ge=0)  # zero valid; negative trips amounts_nonneg
    interest_rate: Decimal | None = Field(default=None, ge=0, le=5)
    # Financing model. When omitted, the tenant's default_calc_formula /
    # default_markup_pct (TenantSettings) apply.
    calc_formula:  Literal["none", "markup", "annuity", "differentiated"] | None = None
    markup_pct:    Decimal | None = Field(default=None, ge=0, le=200)
    start_date:    date | None = None
    items:         list[InstallmentItemIn] = Field(min_length=1, max_length=_MAX_ITEMS)
    # Round 22 — guarantors (kafil) attached to this installment, in order.
    guarantor_ids: list[UUID] = Field(default_factory=list)
    notes:         str | None = Field(default=None, max_length=1500)

def _installment_dict(ins: Installment, customer_name: str | None = None,
                      customer_phone: str | None = None) -> dict:
    return {
        "id":              str(ins.id),
        "reference":       ins.reference,
        "contract_number": ins.contract_number,
        "customer_id":     str(ins.customer_id),
        "customer_name":   customer_name,
        "customer_phone":  customer_phone,
        "status":          ins.status.value,
        # The currency this contract was written in. Money labels read
        # this, not the shop's current setting.
        "currency":        ins.currency,
        "total_amount":    int(ins.total_amount or 0),
        "down_payment":    int(ins.down_payment or 0),
        "principal":       int(ins.principal or 0),
        # Financing cost = total repayable minus the financed cash amount.
        "markup_amount":   int((ins.principal or 0) - ((ins.total_amount or 0) - (ins.down_payment or 0))),
        "calc_formula":    ins.calc_formula,
        "markup_pct":      float(ins.markup_pct) if ins.markup_pct is not None else None,
        "interest_rate":   float(ins.interest_rate) if ins.interest_rate is not None else None,
        "months":          ins.months,
        "monthly_amount":  int(ins.monthly_amount or 0),
        "paid_count":      ins.paid_count,
        "paid_amount":     int(ins.paid_amount or 0),
        "start_date":      ins.start_date.isoformat() if ins.start_date else None,
        "next_due_date":   ins.next_due_date.isoformat() if ins.next_due_date else None,
        "created_at":      ins.created_at.isoformat(),
    }

async def _next_contract_number(db: AsyncSession, tenant: Tenant) -> str:
    """Generate ``MDD-{year}-{6digits}`` — the next sequence for this tenant/year.

    Uses ``MAX(sequence) + 1`` parsed from existing numbers rather than a row
    COUNT: a COUNT reuses a number after any contract is deleted, which would
    collide with the historical contract. Combined with the unique index on
    ``(tenant_id, contract_number)`` and a retry at the insert site, this is
    safe under concurrency too (the loser re-reads MAX and gets the next value).
    """
    year = datetime.now(UTC).year
    prefix = f"MDD-{year}-"
    last = (await db.execute(
        select(func.coalesce(
            func.max(cast(func.split_part(Installment.contract_number, "-", 3), Integer)), 0,
        )).where(
            Installment.tenant_id == tenant.id,
            Installment.contract_number.like(f"{prefix}%"),
        )
    )).scalar_one()
    return f"{prefix}{int(last) + 1:06d}"

@router.get("/installments", dependencies=[search_limit_if_query()])
async def list_installments(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = None,
    #: (MUDD-83) A `catalog_categories.slug`. Matches a contract if ANY of its
    #: lines carries that category — a contract can sell a phone and a laptop
    #: together, and both filters should find it.
    category: str | None = None,
    q: SearchQ = None,
    limit: LimitQ = 50, offset: OffsetQ = 0,
) -> dict:
    tenant, _ = ctx
    stmt = select(Installment).where(Installment.tenant_id == tenant.id).order_by(Installment.created_at.desc())
    if status_filter:
        try:
            stmt = stmt.where(Installment.status == InstallmentStatus(status_filter))
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_status") from None
    if category and category.strip():
        # EXISTS, not a JOIN. A contract with three phone lines must appear
        # ONCE; joining installment_items would return it three times and make
        # `total`, the page size and the offset all disagree with each other.
        # The `ix_items_installment_category` index serves this directly.
        stmt = stmt.where(
            select(InstallmentItem.id)
            .where(
                InstallmentItem.installment_id == Installment.id,
                InstallmentItem.category == category.strip(),
            )
            .exists()
        )
    if q and q.strip():
        like = f"%{q.strip()}%"
        # Match by contract/reference, or by the customer's name/phone.
        cust_sub = select(Customer.id).where(
            Customer.tenant_id == tenant.id,
            or_(Customer.full_name.ilike(like), Customer.phone.ilike(like)),
        )
        stmt = stmt.where(or_(
            Installment.contract_number.ilike(like),
            Installment.reference.ilike(like),
            Installment.customer_id.in_(cust_sub),
        ))
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    # (#139) Count the FILTERED query so the total matches what's shown (was a
    # tenant-wide count that ignored status_filter/q).
    total = (await db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )).scalar_one()
    names: dict = {}
    phones: dict = {}
    cust_ids = {i.customer_id for i in rows if i.customer_id}
    if cust_ids:
        for c in (await db.execute(select(Customer).where(Customer.id.in_(cust_ids)))).scalars():
            names[c.id] = c.full_name
            phones[c.id] = c.phone
    # (MUDD-83) Tenant-wide category tallies, INDEPENDENT of every filter above
    # — same reasoning as the payments status `counts` (MUDD-84): a filter chip
    # has to show how many rows it would reveal, so it cannot be counted from
    # the already-filtered set, and it must not shrink to zero the moment a
    # different filter is applied. One extra aggregate per request regardless
    # of page size.
    #
    # DISTINCT on the installment id, not a row count: a contract selling two
    # phones is one contract in the `smartfonlar` bucket, and the chip's number
    # has to match how many rows selecting it actually produces.
    category_counts: dict[str, int] = {}
    for slug, cnt in (await db.execute(
        select(InstallmentItem.category, func.count(func.distinct(Installment.id)))
        .join(Installment, Installment.id == InstallmentItem.installment_id)
        .where(
            Installment.tenant_id == tenant.id,
            InstallmentItem.category.is_not(None),
        )
        .group_by(InstallmentItem.category)
    )).all():
        category_counts[slug] = cnt

    return {
        "items": [_installment_dict(i, names.get(i.customer_id), phones.get(i.customer_id)) for i in rows],
        "total": total,
        "category_counts": category_counts,
    }

@router.get("/worklist")
async def collections_worklist(
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    q: SearchQ = None,
    limit: LimitQ = 20, offset: OffsetQ = 0,
) -> dict:
    """Contracts that need collecting: overdue first, then due today.

    This is the authoritative source for the set — a client must not re-derive
    it from a page of ``/installments``, which is a slice presented as a whole.
    That includes SEARCH: filtering the pages already loaded would search a
    fraction of the queue and quietly report it as the whole answer, so ``q`` is
    applied here, over the tenant's entire due/overdue set, and the counts below
    describe the filtered set.

    Three properties define the classification:

    * **Today is the tenant's today**, from its configured timezone, with a
      fallback only for an unusable value. See ``_tenant_today``.

    * **Overdue is derived from the due date, not only from the status column.**
      ``mark_overdue`` is a nightly cron, so between a due date passing and that
      job running a genuinely overdue contract still carries ``active``. A
      contract is overdue when its next due date is BEFORE the tenant's today
      (whatever the column says) or the column already says so; due-today only
      when the date is exactly today. Mutually exclusive, so the counts always
      sum to total.

    * **Ordering ends in a stable key.** Overdue-first then soonest-due leaves
      ties, and an unstable sort under OFFSET/LIMIT lets a row appear on two
      pages or on none. ``id`` breaks every tie, so paging is a true partition.

    Bounded by construction: the row query is ordered, offset/limited (cap 200
    via ``LimitQ``); the counts are aggregates, not materialised rows; and the
    customer names come from one IN-query over the returned page, so there is no
    N+1 and no unbounded read.
    """
    tenant, _ = ctx
    today = _tenant_today(tenant)

    # A contract needs collecting when it is open and its next payment is due
    # today or earlier. `completed`/`cancelled` are excluded by construction.
    open_and_due = and_(
        Installment.tenant_id == tenant.id,
        Installment.next_due_date.is_not(None),
        Installment.next_due_date <= today,
        Installment.status.in_((InstallmentStatus.active, InstallmentStatus.overdue)),
    )
    if q and q.strip():
        like = f"%{q.strip()}%"
        # Same vocabulary as `/installments`: contract, reference, or the
        # customer's name/phone. The customer sub-select is tenant-scoped, so
        # search cannot reach another shop's customers.
        cust_sub = select(Customer.id).where(
            Customer.tenant_id == tenant.id,
            or_(Customer.full_name.ilike(like), Customer.phone.ilike(like)),
        )
        open_and_due = and_(open_and_due, or_(
            Installment.contract_number.ilike(like),
            Installment.reference.ilike(like),
            Installment.customer_id.in_(cust_sub),
        ))
    # Overdue by date OR by an already-flipped status; due-today is the exact
    # complement, so `overdue + due_today == total` holds regardless of when the
    # nightly job last ran.
    is_overdue = or_(
        Installment.next_due_date < today,
        Installment.status == InstallmentStatus.overdue,
    )

    ordering = (
        case((is_overdue, 0), else_=1),          # overdue first
        Installment.next_due_date.asc(),          # then soonest due
        Installment.id.asc(),                     # then a stable tie-breaker
    )
    rows = (await db.execute(
        select(Installment).where(open_and_due).order_by(*ordering)
        .offset(offset).limit(limit)
    )).scalars().all()

    total, overdue_count = (await db.execute(
        select(
            func.count(Installment.id),
            func.count(Installment.id).filter(is_overdue),
        ).where(open_and_due)
    )).one()

    names: dict = {}
    phones: dict = {}
    cust_ids = {i.customer_id for i in rows if i.customer_id}
    if cust_ids:
        for c in (await db.execute(
            select(Customer).where(Customer.id.in_(cust_ids), Customer.tenant_id == tenant.id)
        )).scalars():
            names[c.id] = c.full_name
            phones[c.id] = c.phone

    return {
        "items": [
            {
                **_installment_dict(i, names.get(i.customer_id), phones.get(i.customer_id)),
                # Derived, so the UI never has to re-guess overdue-ness from a
                # status column the cron may not have reached yet.
                "is_overdue": bool(i.next_due_date and i.next_due_date < today)
                or i.status == InstallmentStatus.overdue,
            }
            for i in rows
        ],
        "total": int(total or 0),
        "overdue_count": int(overdue_count or 0),
        "due_today_count": int(total or 0) - int(overdue_count or 0),
        # When these figures were computed, so a screen that may be up to a
        # minute stale can say so instead of implying "now".
        "as_of": datetime.now(UTC).isoformat(),
        # The day the classification used, in the tenant's own timezone — so a
        # client in another zone cannot silently disagree about "today".
        "as_of_date": today.isoformat(),
        "timezone": _tenant_tz_name(tenant),
    }

@router.post("/installments/{iid}/remind")
async def remind_installment(
    iid: str,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Manually send the appropriate payment reminder to a single customer now.

    Reuses the same templates + placeholders as the automated reminder cron;
    only sends over Telegram if the customer is linked. Returns the phone so
    the UI can offer a `tel:` call fallback when no Telegram send happened.
    """
    from app.models import TenantSettings
    from app.services.notifications import (
        installment_pay_deeplink,
        notify_user,
        reminder_context,
        render,
    )
    from app.services.scheduler import default_reminder_templates

    tenant, _ = ctx
    try:
        iid_u = UUID(iid)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_id") from None
    ins = (await db.execute(
        select(Installment).where(Installment.id == iid_u, Installment.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")
    customer = (await db.execute(
        select(Customer).where(Customer.id == ins.customer_id)
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")

    # This chooses what the CUSTOMER is told — "due today" versus "overdue" —
    # so it MUST use the tenant's date. The server's date would tell someone in
    # UTC+5 they were late on the evening the payment was actually due.
    today = _tenant_today(tenant)
    offset = (ins.next_due_date - today).days if ins.next_due_date else 0
    tpl_key = "before_due" if offset > 0 else "due_today" if offset == 0 else "overdue"

    s = (await db.execute(
        select(TenantSettings).where(TenantSettings.tenant_id == tenant.id)
    )).scalar_one_or_none()
    templates = (s.reminder_templates if s else {}) or {}
    remaining = ins.remaining_balance
    # (#MUDD-111) The shared builder — the third and last place that used to
    # hand-roll this dict. Its own version formatted no money and supplied
    # neither `days_left`/`contract` (which the Settings UI advertises) nor
    # `phone`, so a manual "remind" could ship a literal placeholder even after
    # the cron was fixed.
    ctxv = reminder_context(
        customer_name=customer.full_name or "",
        currency=ins.currency,           # from the contract, never the shop
        amount=ins.monthly_amount or 0,
        due_date=ins.next_due_date.isoformat() if ins.next_due_date else "",
        contract_number=ins.contract_number or "",
        remaining=remaining,
        days_overdue=max(0, -offset),
        days_until_due=max(0, offset),
        phone=customer.phone or "",
    )
    tpl = templates.get(tpl_key) or default_reminder_templates().get(tpl_key, "")
    body = render(tpl, **ctxv)

    sent = False
    if customer.telegram_id:
        recipient = (await db.execute(
            select(User).where(User.telegram_id == customer.telegram_id)
        )).scalar_one_or_none()
        if recipient is not None:
            sent = await notify_user(
                db,
                user_id=recipient.id,
                tenant_id=tenant.id,
                kind=f"reminder.manual.{tpl_key}",
                title="To'lov eslatmasi",
                installment_id=ins.id,
                body=body,
                # Same one-tap action the 09:00 cron's reminder carries. Without
                # it, a manually-nudged customer got the text and no way to act
                # on it, which is the opposite of why a shop taps "remind".
                link=installment_pay_deeplink(ins.id),
            )
    return {"sent": sent, "channel": "telegram" if sent else None, "phone": customer.phone}

@router.get("/installments/{iid}")
async def get_installment(
    iid: UUID,
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    payments_limit: LimitQ = 200,
    payments_offset: OffsetQ = 0,
) -> dict:
    tenant, _ = ctx
    ins = (await db.execute(
        select(Installment).where(Installment.id == iid, Installment.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")
    items = await ins.awaitable_attrs.items
    # `items` stays whole: InstallmentIn caps a contract at _MAX_ITEMS line items
    # on both write paths, so it is bounded by the domain, not by usage.
    # (MUDD-85) One batched query for every item's category-linked identifier
    # values, instead of `awaitable_attrs.identifiers` per item — the latter
    # is one query per line and this endpoint is already bounded by item
    # count, not by whether it fetches them one at a time or all at once.
    identifiers_by_item: dict[UUID, dict[str, str]] = {}
    if items:
        id_rows = (await db.execute(
            select(
                InstallmentItemIdentifier.item_id,
                InstallmentItemIdentifier.key,
                InstallmentItemIdentifier.value,
            ).where(InstallmentItemIdentifier.item_id.in_([i.id for i in items]))
        )).all()
        for item_id, key, value in id_rows:
            identifiers_by_item.setdefault(item_id, {})[key] = value
    # `payments` is not — partial payments, rejected attempts and one late-fee row
    # per overdue day accumulate for the life of the contract — so it is read as an
    # explicitly ordered, bounded page rather than the whole relationship.
    payments = (await db.execute(
        select(Payment).where(Payment.installment_id == ins.id)
        .order_by(Payment.created_at, Payment.id)
        .offset(payments_offset).limit(payments_limit)
    )).scalars().all()
    g_rows = (await db.execute(
        select(InstallmentGuarantor, Guarantor)
        .join(Guarantor, Guarantor.id == InstallmentGuarantor.guarantor_id)
        .where(InstallmentGuarantor.installment_id == ins.id)
        .order_by(InstallmentGuarantor.sequence)
    )).all()
    return {
        **_installment_dict(ins),
        "guarantors": [
            {
                "id": str(g.id),
                "sequence": link.sequence,
                "full_name": g.full_name,
                "phone": g.phone,
                "relationship": g.relationship_label,
            } for link, g in g_rows
        ],
        "items": [
            {
                "id": str(i.id),
                "product_id": str(i.product_id) if i.product_id else None,
                "catalog_model_id": str(i.catalog_model_id) if i.catalog_model_id else None,
                "product_name_snapshot": i.product_name_snapshot,
                "category": i.category,
                "variant_label": i.variant_label,
                "serial_number": i.serial_number,
                "identifier_type": i.identifier_type,
                "identifiers": identifiers_by_item.get(i.id, {}),
                "qty": i.qty,
                "unit_price": int(i.unit_price or 0),
                "line_total": int(i.line_total or 0),
            } for i in items
        ],
        "payments": [
            {
                "id": str(p.id),
                "amount": int(p.amount or 0),
                "method": p.method.value,
                "status": p.status.value,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
            } for p in payments
        ],
    }

@router.post("/installments", status_code=status.HTTP_201_CREATED)
async def create_installment(
    body: InstallmentIn,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    tenant, _ = ctx
    # Sanity: customer belongs to this tenant and is approved.
    customer = (await db.execute(
        select(Customer).where(Customer.id == body.customer_id, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")
    if customer.status == CustomerStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, "customer_pending_approval")
    if customer.status == CustomerStatus.blocked:
        raise HTTPException(status.HTTP_409_CONFLICT, "customer_blocked")
    # A missing telegram_id is deliberately NOT a precondition for issuing a
    # contract: the walk-in sale is the core job and the customer may link
    # Telegram later, or never (the dunning job skips unlinked customers).
    # Isolation is unaffected — the customer is verified to belong to THIS
    # tenant above, and customer WebApp access still binds strictly on
    # Customer.telegram_id == user.telegram_id (app/api/me.py::_my_customer_rows),
    # so linking later grants exactly that customer's own contracts.

    # Plan limit: a new contract counts toward the active-contract quota. A hard
    # limit blocks; a soft limit with overage is allowed and flagged so the UI
    # can warn that it will be billed as overage.
    from app.services import billing, plan_limits
    # (#110) Serialize the count→check→insert against concurrent creates for the
    # SAME tenant with a transaction-scoped advisory lock. Without it, two
    # simultaneous requests both read active_now = N-1, both pass the check, and
    # both insert → the tenant blows past a hard plan cap. The lock is held until
    # this request's transaction commits (the row is inserted below in the same
    # unit of work), so a competing request blocks until it can see our insert.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"contract_limit:{tenant.id}"},
    )
    # (F-12) Idempotent replay: a retried or double-submitted create carrying the
    # same Idempotency-Key returns the already-created contract instead of issuing a
    # duplicate. The advisory lock above serialises same-tenant creates, so by the
    # time we read here any concurrent first request has committed and is visible;
    # uq_installments_idem (set on the insert below) is the durable DB backstop.
    if idempotency_key is not None:
        existing = (await db.execute(
            select(Installment).where(
                Installment.tenant_id == tenant.id,
                Installment.idempotency_key == idempotency_key,
            )
        )).scalar_one_or_none()
        if existing is not None:
            return _installment_dict(existing)
    active_now = await billing.active_installment_count(db, tenant.id)
    decision = await plan_limits.check_limit(db, tenant, "contract", active_now, 1)
    if not decision.allowed:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            # `message` is server prose kept for logs and older clients; the
            # structured fields are what a localized UI should render.
            detail={
                "error": "plan_limit_reached", "limit_key": "contract",
                "limit": decision.limit, "message": decision.message,
            },
        )
    overage_note = None
    if decision.over:
        overage_note = {
            "message": decision.message,
            "extra_message": (
                f"Bu shartnoma tarif limitidan tashqari hisoblanadi. "
                f"Qo'shimcha narx: {decision.overage_price} so'm / oy."
            ),
            "overage_price": decision.overage_price,
        }

    total = sum((it.unit_price * it.qty for it in body.items), Decimal("0"))

    # Apply the financing model (markup / interest). When the request doesn't
    # specify, fall back to the tenant's configured defaults. Without this the
    # shop's margin was silently dropped (every contract interest-free — C1).
    from app.models import TenantSettings as _TS
    from app.services import calc as _calc
    _ts = (await db.execute(
        select(_TS).where(_TS.tenant_id == tenant.id)
    )).scalar_one_or_none()
    calc_formula = body.calc_formula or (_ts.default_calc_formula if _ts else None) or "none"
    markup_pct = (
        body.markup_pct if body.markup_pct is not None
        else (_ts.default_markup_pct if _ts else None)
    )
    try:
        sched = _calc.compute_schedule(
            goods_total=total, down_payment=body.down_payment, months=body.months,
            formula=calc_formula, markup_pct=markup_pct, interest_rate=body.interest_rate,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    # The tenant's date: a contract created at 21:00 in Tashkent would otherwise
    # be dated yesterday, shifting every due date in the schedule.
    start = body.start_date or _tenant_today(tenant)

    # Snapshot the shop's currency onto the contract. Read once, here,
    # at the moment the obligation is created — a later change to the setting
    # decides what the NEXT contract says and must not touch this one. `_ts` is
    # the same settings row the financing defaults above came from.
    from app.core.currency import currency_for_new_contract
    from app.services.installments import add_months

    ins = Installment(
        tenant_id=tenant.id, customer_id=customer.id, sold_by_user_id=user.id,
        status=InstallmentStatus.active,
        currency=currency_for_new_contract(_ts),
        total_amount=sched.total_amount, down_payment=body.down_payment,
        principal=sched.principal, monthly_amount=sched.monthly_amount,
        months=body.months, interest_rate=body.interest_rate,
        calc_formula=sched.formula, markup_pct=markup_pct,
        start_date=start,
        # (MUDD-58) Was a hand-rolled "+1 month, clamp day to 28" that shrank
        # every 29th/30th/31st-of-the-month start date instead of rolling to the
        # target month's actual last day (31 Mar -> 28 Apr instead of 30 Apr),
        # pulling the customer's first due date 2 days early. `add_months`
        # clamps against the real month length via `calendar.monthrange`.
        next_due_date=add_months(start, 1),
        paid_count=0, paid_amount=Decimal("0"), notes=body.notes,
        idempotency_key=idempotency_key,
    )
    db.add(ins)
    # Allocate the contract number with a SAVEPOINT retry: if a concurrent sale
    # grabbed the same number, the unique index rejects the flush, the savepoint
    # rolls back (the outer tx survives), and we re-read MAX for the next value.
    for attempt in range(5):
        ins.contract_number = await _next_contract_number(db, tenant)
        try:
            async with db.begin_nested():
                await db.flush()
            break
        except IntegrityError:
            if attempt == 4:
                raise HTTPException(status.HTTP_409_CONFLICT, "contract_number_conflict") from None

    from app.services import inventory

    # (audit/sec) Validate every product_id belongs to THIS tenant before it lands
    # in an InstallmentItem. A foreign id would otherwise leak the other tenant's
    # brand/category/cost_price through the reports joins (RLS blocks the live read,
    # but the app-layer defense must not have the hole). Mirrors guarantor validation.
    # The same batch feeds the stock ledger below: loading the rows rather than just
    # their ids means record_movement doesn't re-read one product per line item — a
    # 50-item sale issued 50 SELECTs for products this one query had already read.
    _item_pids = {it.product_id for it in body.items if it.product_id is not None}
    _products_by_id: dict[UUID, Product] = {}
    if _item_pids:
        _products_by_id = {p.id: p for p in (await db.execute(
            select(Product).where(Product.tenant_id == tenant.id, Product.id.in_(_item_pids))
        )).scalars()}
        if _item_pids - set(_products_by_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "product_not_found")

    item_categories = await _resolve_item_categories(db, body.items, _products_by_id)
    item_identifiers = await _validate_item_identifiers(
        db, body.items, item_categories, tenant.id,
    )

    for it, item_category, item_ids in zip(
        body.items, item_categories, item_identifiers, strict=True,
    ):
        item_id = uuid4()
        db.add(InstallmentItem(
            id=item_id,
            installment_id=ins.id,
            product_id=it.product_id,
            catalog_model_id=it.catalog_model_id,
            product_name_snapshot=it.product_name_snapshot,
            category=item_category,
            variant_label=it.variant_label,
            serial_number=it.serial_number,
            identifier_type=it.identifier_type,
            qty=it.qty, unit_price=it.unit_price,
            line_total=it.unit_price * it.qty,
        ))
        for key, value in item_ids.items():
            db.add(InstallmentItemIdentifier(
                tenant_id=tenant.id, item_id=item_id, key=key, value=value,
            ))
        # Auto stock-out for tracked products (negative qty = sale).
        if it.product_id is not None:
            await inventory.record_movement(
                db, tenant_id=tenant.id, product_id=it.product_id,
                qty=-it.qty, kind=StockMovementKind.sale,
                by_user_id=user.id, related_installment_id=ins.id,
                product=_products_by_id.get(it.product_id),
            )

    # Attach guarantors (validated to belong to this tenant), preserving order.
    if body.guarantor_ids:
        valid_ids = set((await db.execute(
            select(Guarantor.id).where(
                Guarantor.tenant_id == tenant.id,
                Guarantor.id.in_(body.guarantor_ids),
            )
        )).scalars().all())
        for seq, gid in enumerate(body.guarantor_ids, start=1):
            if gid not in valid_ids:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "guarantor_not_found")
            db.add(InstallmentGuarantor(
                installment_id=ins.id, guarantor_id=gid, sequence=seq,
            ))
    # (MUDD-85) The pre-check above is best-effort under concurrency — two
    # requests can both pass it before either commits. The DB constraint is
    # what actually decides; a violation here means a race, not a bug.
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_item_identifier_tenant_key_value" in str(exc.orig):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "category_identifier_duplicate",
            ) from None
        raise
    await db.refresh(ins)
    result = _installment_dict(ins)
    if overage_note:
        result["overage"] = overage_note

    # (r96) Tell the customer, in Telegram, that a contract now exists in their
    # name — with the products on it and a way to reach the shop if it wasn't
    # them. AFTER the commit and deliberately best-effort: the contract is real
    # whether or not the message lands, so a Telegram outage must not roll it
    # back or 500 the operator. `notify_new_contract` returns False rather than
    # raising when there is nobody to reach.
    from app.services import customer_outreach
    try:
        result["customer_notified"] = await customer_outreach.notify_new_contract(
            db, tenant=tenant, customer=customer, installment=ins,
        )
    except Exception:
        logging.getLogger(__name__).exception(
            "installment.notify_failed installment=%s", ins.id)
        result["customer_notified"] = False
    return result

class CancelIn(BaseModel):
    reason: str = Field(default="", max_length=500)

@router.post("/installments/{iid}/cancel")
async def cancel_installment(
    iid: UUID,
    body: CancelIn,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Cancel an active/overdue contract with a reason (kept in notes + audit).
    Unlike delete, this preserves the record."""
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    # (#751) Lock the row so this cancel serializes with the payment webhooks
    # (Payme/Click handlers SELECT ... FOR UPDATE the same installment). Without
    # the lock a webhook crediting/cancelling the contract can interleave with
    # this status flip, and two concurrent cancels both pass the guards below
    # (TOCTOU) → double audit + double processing.
    ins = (await db.execute(
        select(Installment)
        .where(Installment.id == iid, Installment.tenant_id == tenant.id)
        .with_for_update()
    )).scalar_one_or_none()
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")
    if ins.status == InstallmentStatus.completed:
        raise HTTPException(status.HTTP_409_CONFLICT, "already_completed")
    if ins.status == InstallmentStatus.cancelled:
        raise HTTPException(status.HTTP_409_CONFLICT, "already_cancelled")
    ins.status = InstallmentStatus.cancelled
    ins.closed_at = datetime.now(UTC)
    reason = (body.reason or "").strip()
    if reason:
        stamp = f"[bekor qilindi] {reason}"
        ins.notes = f"{ins.notes}\n{stamp}" if ins.notes else stamp
    from app.services import audit
    await audit.record(
        db, action="installment.cancel", tenant_id=tenant.id, actor_user_id=user.id,
        entity_type="installment", entity_id=str(ins.id), after={"reason": reason},
    )
    await db.commit()
    await db.refresh(ins)
    return _installment_dict(ins)

class RescheduleIn(BaseModel):
    add_months:    int = Field(default=0, ge=0, le=60)
    next_due_date: date | None = None

@router.post("/installments/{iid}/reschedule")
async def reschedule_installment(
    iid: UUID,
    body: RescheduleIn,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Restructure an open contract: extend the term and/or push the next due
    date. Monthly is re-derived from the remaining balance over remaining months."""
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    # (audit/sec) Lock the row so reschedule serializes with concurrent payment
    # webhooks (which also lock the installment); without FOR UPDATE the service
    # re-anchors paid progress off an unlocked snapshot → lost update / corrupt
    # paid_count. Mirrors cancel_installment.
    ins = (await db.execute(
        select(Installment).where(Installment.id == iid, Installment.tenant_id == tenant.id)
        .with_for_update()
    )).scalar_one_or_none()
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")
    if ins.status in (InstallmentStatus.completed, InstallmentStatus.cancelled):
        raise HTTPException(status.HTTP_409_CONFLICT, "not_open")
    # (#119) Delegate to the service so monthly/principal are re-derived AND the
    # paid progress is re-anchored — recomputing schedule state from paid_amount
    # without this corrupted paid_count on the next payment.
    from app.services.installments import reschedule
    reschedule(
        ins,
        add_months=body.add_months,
        next_due_date=body.next_due_date,
        now=datetime.now(UTC),
    )
    from app.services import audit
    await audit.record(
        db, action="installment.reschedule", tenant_id=tenant.id, actor_user_id=user.id,
        entity_type="installment", entity_id=str(ins.id),
        after={"add_months": body.add_months, "months": ins.months,
               "next_due_date": ins.next_due_date.isoformat() if ins.next_due_date else None},
    )
    await db.commit()
    await db.refresh(ins)
    return _installment_dict(ins)

@router.put("/installments/{iid}")
async def replace_installment(
    iid: UUID,
    body: InstallmentIn,
    user: Annotated[User, Depends(current_user)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Full edit of a not-yet-paid installment (fix a wrong price/term without
    delete+recreate). Allowed only while no payment has been recorded."""
    tenant, membership = ctx
    _require_owner_or_manager(membership)
    # (MUDD-63/H3) Lock the row so this edit serializes with concurrent payment
    # webhooks (which also lock the installment) — mirrors cancel_installment
    # and reschedule_installment above. Without FOR UPDATE, a payment can land
    # between this SELECT and the writes below: the has_payments guard passes
    # on a stale paid_count/paid_amount, then this handler overwrites the row
    # with a freshly recomputed schedule, silently discarding the payment.
    ins = (await db.execute(
        select(Installment).where(Installment.id == iid, Installment.tenant_id == tenant.id)
        .with_for_update()
    )).scalar_one_or_none()
    if ins is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "installment_not_found")
    if (ins.paid_count or 0) > 0 or (ins.paid_amount or 0) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "has_payments")
    if ins.status in (InstallmentStatus.completed, InstallmentStatus.cancelled):
        raise HTTPException(status.HTTP_409_CONFLICT, "not_editable")

    customer = (await db.execute(
        select(Customer).where(Customer.id == body.customer_id, Customer.tenant_id == tenant.id)
    )).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer_not_found")

    total = sum((it.unit_price * it.qty for it in body.items), Decimal("0"))

    from app.models import TenantSettings as _TS
    from app.services import calc as _calc
    _ts = (await db.execute(
        select(_TS).where(_TS.tenant_id == tenant.id)
    )).scalar_one_or_none()
    calc_formula = body.calc_formula or (_ts.default_calc_formula if _ts else None) or "none"
    markup_pct = (
        body.markup_pct if body.markup_pct is not None
        else (_ts.default_markup_pct if _ts else None)
    )
    try:
        sched = _calc.compute_schedule(
            goods_total=total, down_payment=body.down_payment, months=body.months,
            formula=calc_formula, markup_pct=markup_pct, interest_rate=body.interest_rate,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    start = body.start_date or ins.start_date or _tenant_today(tenant)

    ins.customer_id = customer.id
    ins.total_amount = sched.total_amount
    ins.down_payment = body.down_payment
    ins.principal = sched.principal
    ins.months = body.months
    ins.monthly_amount = sched.monthly_amount
    ins.interest_rate = body.interest_rate
    ins.calc_formula = sched.formula
    ins.markup_pct = markup_pct
    ins.start_date = start
    # (MUDD-58) Same fix as create_installment — see the comment there.
    from app.services.installments import add_months
    ins.next_due_date = add_months(start, 1)
    ins.notes = body.notes

    # (MUDD-123) Read what the lines already carry BEFORE the delete below wipes
    # it — the identifier rows go with the items via ON DELETE CASCADE, and after
    # that nothing knows which of today's rules this contract predates.
    stored_lines = await _load_stored_item_identifiers(db, ins.id)

    # Replace items + guarantors (products are catalog/free-text now → no stock).
    await db.execute(sa_delete(InstallmentItem).where(InstallmentItem.installment_id == ins.id))
    await db.execute(sa_delete(InstallmentGuarantor).where(InstallmentGuarantor.installment_id == ins.id))
    # (audit/sec) Same tenant-ownership check as create_installment — a foreign
    # product_id must not be plantable into this tenant's InstallmentItems.
    _item_pids = {it.product_id for it in body.items if it.product_id is not None}
    # (MUDD-83) Load the rows, not just their ids: the ownership check needs the
    # ids, but `_resolve_item_categories` needs `products.category` off the same
    # rows — re-reading them per line item is the exact pattern create_installment
    # already avoids.
    _products_by_id: dict[UUID, Product] = {}
    if _item_pids:
        _products_by_id = {p.id: p for p in (await db.execute(
            select(Product).where(Product.tenant_id == tenant.id, Product.id.in_(_item_pids))
        )).scalars()}
        if _item_pids - set(_products_by_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "product_not_found")
    item_categories = await _resolve_item_categories(db, body.items, _products_by_id)
    # (MUDD-85/MUDD-123) Old items were just deleted above (CASCADE already
    # cleared their identifier rows), so every line re-enters the same check a
    # fresh create gets — except that a line REPLACING an existing one is judged
    # against what that line was actually given a chance to record. A line this
    # edit ADDS still faces the schema in full. See `_grandfathered_identifiers`.
    item_identifiers = await _validate_item_identifiers(
        db, body.items, item_categories, tenant.id,
        grandfathered=_grandfathered_identifiers(
            stored_lines, body.items, item_categories,
        ),
    )
    for it, item_category, item_ids in zip(
        body.items, item_categories, item_identifiers, strict=True,
    ):
        item_id = uuid4()
        db.add(InstallmentItem(
            id=item_id,
            installment_id=ins.id, product_id=it.product_id, catalog_model_id=it.catalog_model_id,
            product_name_snapshot=it.product_name_snapshot, category=item_category,
            variant_label=it.variant_label,
            serial_number=it.serial_number, identifier_type=it.identifier_type,
            qty=it.qty, unit_price=it.unit_price,
            line_total=it.unit_price * it.qty,
        ))
        for key, value in item_ids.items():
            db.add(InstallmentItemIdentifier(
                tenant_id=tenant.id, item_id=item_id, key=key, value=value,
            ))
    if body.guarantor_ids:
        valid_ids = set((await db.execute(
            select(Guarantor.id).where(
                Guarantor.tenant_id == tenant.id, Guarantor.id.in_(body.guarantor_ids),
            )
        )).scalars().all())
        for seq, gid in enumerate(body.guarantor_ids, start=1):
            # (LOW-2) Same answer as create_installment: an id this tenant does not
            # own is a 404, not a silent omission. Dropping it returned 200 for an
            # edit that did not do what was asked — the contract came back with
            # fewer guarantors than the operator submitted and nothing said so.
            if gid not in valid_ids:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "guarantor_not_found")
            db.add(InstallmentGuarantor(installment_id=ins.id, guarantor_id=gid, sequence=seq))

    from app.services import audit
    await audit.record(
        db, action="installment.edit", tenant_id=tenant.id, actor_user_id=user.id,
        entity_type="installment", entity_id=str(ins.id), after={"principal": int(sched.principal)},
    )
    # (MUDD-85) Same race-safety backstop as create_installment.
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if "uq_item_identifier_tenant_key_value" in str(exc.orig):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "category_identifier_duplicate",
            ) from None
        raise
    await db.refresh(ins)
    return _installment_dict(ins)
