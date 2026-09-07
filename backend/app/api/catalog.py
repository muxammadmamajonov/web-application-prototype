"""Global product catalog endpoints.

Read endpoints (`GET /catalog/…`) are **public** — anyone (even unauthenticated
tenant signup flows) needs to see what models exist.

Write endpoints (`POST/PATCH/DELETE`) are platform-admin only.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.tenant import _require_owner_or_manager, resolve_tenant
from app.core.authz import require_platform_admin
from app.core.ratelimit import upload_limit
from app.core.security import assert_token_current, current_user, decode_token
from app.db import get_db, set_identity_scope
from app.models import (
    CatalogBrand,
    CatalogCategory,
    CatalogColor,
    CatalogModel,
    Tenant,
    TenantUser,
    User,
)
from app.schemas.common import LimitQ, OffsetQ
from app.services.media import upload_image as _store_image

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _slugify(s: str) -> str:
    import re
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", s.lower().strip())).strip("-")[:80] or "model"


async def _optional_tenant_id(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
) -> UUID | None:
    """Resolve the caller's active tenant for catalog reads — best-effort, never
    raises. Used so the picker can include a tenant's own *proposed* models
    without leaking other tenants' proposals. Public/anonymous callers get None
    (global catalog only).

    (MUDD-57 M-15) "Best-effort" governs the FAILURE path only. Success here is a
    privileged outcome — it binds the caller to ``X-Tenant-Id`` and unlocks that
    tenant's proposed models — so it must clear the same bar as ``current_user``.
    A decoded signature alone does not: it stays valid after `logout` /
    `logout-all` and after the employee is deactivated. ``assert_token_current``
    is the shared gate; anything it rejects degrades to anonymous (None).
    """
    if not authorization or not authorization.lower().startswith("bearer ") or not x_tenant_id:
        return None
    try:
        payload = decode_token(authorization.split(" ", 1)[1])
        uid = payload.get("sub")
        tid = UUID(x_tenant_id)
    except Exception:
        return None
    if not uid:
        return None
    try:
        # Pin the DB identity from the verified signature before reading the
        # account, exactly as ``current_user`` does — under the fail-closed
        # `users` policy an unscoped session sees no user row at all, and this
        # also scopes the membership lookup below to the caller's own rows.
        await set_identity_scope(db, uid)
        user = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
        if user is None:
            return None
        assert_token_current(payload, user)
    except HTTPException:
        # Deactivated account or revoked/logged-out token → anonymous, not scoped.
        return None
    member = (await db.execute(
        select(TenantUser.id).where(
            TenantUser.user_id == uid, TenantUser.tenant_id == tid,
            TenantUser.is_active.is_(True),
        )
    )).first()
    return tid if member else None


@router.post("/upload-image", dependencies=[upload_limit()])
async def upload_image(
    user: Annotated[User, Depends(current_user)],
    file: Annotated[UploadFile, File()],
) -> dict:
    """Platform admin — store a brand logo / category / model image on archive.org."""
    require_platform_admin(user)
    from app.core.limits import MAX_UPLOAD_BYTES, read_capped
    data = await read_capped(file, MAX_UPLOAD_BYTES)  # (#88) cap before buffering
    url = await _store_image(data, file.content_type)
    return {"url": url}


# ── public reads ─────────────────────────────────────────────────────────────

def _category_dict(c: CatalogCategory, brand_count: int = 0) -> dict:
    return {
        "id":         str(c.id),
        "slug":       c.slug,
        "name_uz":    c.name_uz,
        "name_ru":    c.name_ru,
        "icon_key":   c.icon_key,
        "icon_emoji": c.icon_emoji,
        "icon_url":   c.icon_url,
        "attribute_schema":  c.attribute_schema or [],
        "identifier_schema": c.identifier_schema or [],
        "business_types":    c.business_types or [],
        "sort_order": c.sort_order,
        "is_active":  c.is_active,
        "brand_count": brand_count,
    }


def _brand_dict(b: CatalogBrand, model_count: int = 0) -> dict:
    return {
        "id":           str(b.id),
        "category_id":  str(b.category_id) if b.category_id else None,
        "category_ids": [str(c) for c in (b.category_ids or [])],
        "slug":         b.slug,
        "name":         b.name,
        "logo_url":     b.logo_url,
        "sort_order":   b.sort_order,
        "is_active":    b.is_active,
        "model_count":  model_count,
    }


def _model_dict(m: CatalogModel) -> dict:
    return {
        "id":                  str(m.id),
        "brand_id":            str(m.brand_id),
        "category_id":         str(m.category_id) if m.category_id else None,
        "slug":                m.slug,
        "name":                m.name,
        "release_year":        m.release_year,
        "image_url":           m.image_url,
        "attributes_template": m.attributes_template or {},
        "sort_order":          m.sort_order,
        "is_active":           m.is_active,
        "status":              m.status,
        "tenant_id":           str(m.tenant_id) if m.tenant_id else None,
    }


def _color_dict(c: CatalogColor) -> dict:
    return {
        "id": str(c.id), "slug": c.slug, "name_uz": c.name_uz, "name_ru": c.name_ru,
        "hex": c.hex, "sort_order": c.sort_order, "is_active": c.is_active,
    }


@router.get("/categories")
async def list_categories(
    db: Annotated[AsyncSession, Depends(get_db)],
    business_type: str | None = None,
) -> list[dict]:
    """Categories + their brand counts in a single round-trip.

    When ``business_type`` is given (and not the reserved ``general``/``other``),
    only categories tagged with that segment — plus untagged "universal"
    categories — are returned, so a furniture shop doesn't wade through phones.
    """
    cats = (await db.execute(
        select(CatalogCategory).where(CatalogCategory.is_active.is_(True))
        .order_by(CatalogCategory.sort_order, CatalogCategory.name_uz)
    )).scalars().all()
    # Brand counts honour multi-category membership (category_ids), so a brand
    # shared by several categories is counted in each. Catalog is small enough
    # to tally in Python rather than a JSON-containment join.
    brands = (await db.execute(
        select(CatalogBrand).where(CatalogBrand.is_active.is_(True))
    )).scalars().all()
    counts: dict[str, int] = {}
    for b in brands:
        for cid in (b.category_ids or ([b.category_id] if b.category_id else [])):
            counts[str(cid)] = counts.get(str(cid), 0) + 1
    out = [_category_dict(c, brand_count=counts.get(str(c.id), 0)) for c in cats]
    if business_type and business_type not in ("general", "other"):
        out = [
            c for c in out
            if not c["business_types"] or business_type in c["business_types"]
        ]
    return out


@router.get("/categories/{slug}/brands")
async def list_brands_for_category(
    slug: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[dict]:
    cat = (await db.execute(select(CatalogCategory).where(CatalogCategory.slug == slug))).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category_not_found")
    cid = str(cat.id)
    all_brands = (await db.execute(
        select(CatalogBrand).where(CatalogBrand.is_active.is_(True))
        .order_by(CatalogBrand.sort_order, CatalogBrand.name)
    )).scalars().all()
    brands = [
        b for b in all_brands
        if cid in [str(x) for x in (b.category_ids or [])]
        or (b.category_id and str(b.category_id) == cid)
    ]
    # Model counts scoped to this category (a brand's models can span categories).
    counts: dict[UUID, int] = {
        brand_id: n
        for brand_id, n in (await db.execute(
            select(CatalogModel.brand_id, func.count(CatalogModel.id))
            .where(CatalogModel.category_id == cat.id)
            .group_by(CatalogModel.brand_id)
        )).all()
    }
    return [_brand_dict(b, model_count=int(counts.get(b.id, 0))) for b in brands]


@router.get("/brands")
async def list_brands(
    db: Annotated[AsyncSession, Depends(get_db)],
    category_id: UUID | None = None,
    sort: str = Query(default="name_asc"),
) -> list[dict]:
    """(#199) All active brands in ONE query with batched model counts — replaces
    the per-category fan-out (1 + N round-trips) the brands page used to make.
    Optional category_id filter + name asc/desc sort for the platform brands list."""
    q = select(CatalogBrand).where(CatalogBrand.is_active.is_(True))
    if sort == "name_desc":
        q = q.order_by(CatalogBrand.name.desc())
    else:
        q = q.order_by(CatalogBrand.sort_order, CatalogBrand.name)
    brands = (await db.execute(q)).scalars().all()
    if category_id is not None:
        cid = str(category_id)
        brands = [
            b for b in brands
            if cid in [str(x) for x in (b.category_ids or [])]
            or (b.category_id and str(b.category_id) == cid)
        ]
    # One GROUP BY for every brand's model count — no per-brand query (was N+1).
    counts: dict[UUID, int] = {
        bid: int(n)
        for bid, n in (await db.execute(
            select(CatalogModel.brand_id, func.count(CatalogModel.id))
            .group_by(CatalogModel.brand_id)
        )).all()
    }
    return [_brand_dict(b, model_count=counts.get(b.id, 0)) for b in brands]


@router.get("/brands/{brand_id}/models")
async def list_models_for_brand(
    brand_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[UUID | None, Depends(_optional_tenant_id)],
    category_id: UUID | None = None,
) -> list[dict]:
    q = select(CatalogModel).where(
        CatalogModel.brand_id == brand_id,
        CatalogModel.is_active.is_(True),
        # Global models (tenant_id NULL) + the caller's own proposed models.
        or_(CatalogModel.tenant_id.is_(None), CatalogModel.tenant_id == tenant_id),
    )
    if category_id is not None:
        q = q.where(CatalogModel.category_id == category_id)
    rows = (await db.execute(
        q.order_by(CatalogModel.sort_order, CatalogModel.name)
    )).scalars().all()
    return [_model_dict(m) for m in rows]


@router.get("/models")
async def list_models(
    db: Annotated[AsyncSession, Depends(get_db)],
    category_id: UUID | None = None,
    brand_id: UUID | None = None,
    q: str | None = None,
    limit: LimitQ = 50,
    offset: OffsetQ = 0,
) -> list[dict]:
    """Flat model list for the platform Products subpage — filter by category /
    brand and free-text search the name/slug. (#78) Public endpoint: tightened
    from a 500/1000 cap to 50/200 with offset paging."""
    # Flat list is platform-admin oriented but reachable by tenants; only show
    # global models here (proposed models surface in the brand-scoped picker).
    query = select(CatalogModel).where(CatalogModel.tenant_id.is_(None))
    if category_id is not None:
        query = query.where(CatalogModel.category_id == category_id)
    if brand_id is not None:
        query = query.where(CatalogModel.brand_id == brand_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            func.lower(CatalogModel.name).like(func.lower(like))
            | func.lower(CatalogModel.slug).like(func.lower(like))
        )
    rows = (await db.execute(
        query.order_by(CatalogModel.sort_order, CatalogModel.name, CatalogModel.id)
        .offset(offset).limit(limit)
    )).scalars().all()
    return [_model_dict(m) for m in rows]


@router.get("/models/{model_id}")
async def get_model(
    model_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[UUID | None, Depends(_optional_tenant_id)],
) -> dict:
    # (F-25) Scope the by-id read to global catalog rows (tenant_id NULL) + the
    # caller's own proposed models — same predicate as the list endpoints — so a
    # tenant's proposed model isn't readable by anyone who guesses its UUID.
    m = (await db.execute(
        select(CatalogModel).where(
            CatalogModel.id == model_id,
            or_(CatalogModel.tenant_id.is_(None), CatalogModel.tenant_id == tenant_id),
        )
    )).scalar_one_or_none()
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "model_not_found")
    return _model_dict(m)


# ── platform-admin writes ────────────────────────────────────────────────────

class CategoryIn(BaseModel):
    slug:       str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9-]+$")
    name_uz:    str = Field(min_length=1, max_length=80)
    name_ru:    str = Field(min_length=1, max_length=80)
    icon_key:   str | None = None
    icon_emoji: str | None = Field(default=None, max_length=16)
    icon_url:   str | None = Field(default=None, max_length=500)
    attribute_schema:  list = Field(default_factory=list)
    identifier_schema: list = Field(default_factory=list)
    business_types:    list = Field(default_factory=list)
    sort_order: int = 100
    is_active:  bool = True


class BrandIn(BaseModel):
    category_ids: list[UUID] = Field(min_length=1)
    slug:         str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9-]+$")
    name:         str = Field(min_length=1, max_length=80)
    logo_url:     str | None = None
    sort_order:   int = 100
    is_active:    bool = True


class ModelIn(BaseModel):
    brand_id:            UUID
    category_id:         UUID
    slug:                str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    name:                str = Field(min_length=1, max_length=120)
    release_year:        int | None = None
    image_url:           str | None = None
    attributes_template: dict = Field(default_factory=dict)
    sort_order:          int = 100
    is_active:           bool = True


@router.post("/categories", status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    require_platform_admin(user)
    cat = CatalogCategory(**body.model_dump())
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return _category_dict(cat)


@router.patch("/categories/{cat_id}")
async def update_category(
    cat_id: UUID,
    body: CategoryIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    require_platform_admin(user)
    cat = (await db.execute(select(CatalogCategory).where(CatalogCategory.id == cat_id))).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category_not_found")
    for k, v in body.model_dump().items():
        setattr(cat, k, v)
    await db.commit()
    await db.refresh(cat)
    return _category_dict(cat)


@router.delete("/categories/{cat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    cat_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    require_platform_admin(user)
    cat = (await db.execute(select(CatalogCategory).where(CatalogCategory.id == cat_id))).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category_not_found")
    await db.delete(cat)
    await db.commit()


@router.post("/brands", status_code=status.HTTP_201_CREATED)
async def create_brand(
    body: BrandIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    require_platform_admin(user)
    cids = [str(c) for c in body.category_ids]
    b = CatalogBrand(
        slug=body.slug, name=body.name, logo_url=body.logo_url,
        sort_order=body.sort_order, is_active=body.is_active,
        category_ids=cids, category_id=body.category_ids[0],
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return _brand_dict(b)


@router.patch("/brands/{brand_id}")
async def update_brand(
    brand_id: UUID,
    body: BrandIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    require_platform_admin(user)
    b = (await db.execute(select(CatalogBrand).where(CatalogBrand.id == brand_id))).scalar_one_or_none()
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand_not_found")
    b.slug = body.slug
    b.name = body.name
    b.logo_url = body.logo_url
    b.sort_order = body.sort_order
    b.is_active = body.is_active
    b.category_ids = [str(c) for c in body.category_ids]
    b.category_id = body.category_ids[0]
    await db.commit()
    await db.refresh(b)
    return _brand_dict(b)


@router.delete("/brands/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brand(
    brand_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    require_platform_admin(user)
    b = (await db.execute(select(CatalogBrand).where(CatalogBrand.id == brand_id))).scalar_one_or_none()
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand_not_found")
    await db.delete(b)
    await db.commit()


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def create_model(
    body: ModelIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    require_platform_admin(user)
    m = CatalogModel(**body.model_dump())
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return _model_dict(m)


class ModelProposeIn(BaseModel):
    """A tenant adds a model that's missing from the global catalog (e.g. a
    brand-new phone). It's saved as a tenant-scoped 'proposed' model — visible
    only to that tenant until the platform promotes it to global."""
    brand_id:            UUID
    category_id:         UUID
    name:                str = Field(min_length=1, max_length=120)
    release_year:        int | None = None
    attributes_template: dict = Field(default_factory=dict)


@router.post("/models/propose", status_code=status.HTTP_201_CREATED)
async def propose_model(
    body: ModelProposeIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[tuple[Tenant, TenantUser], Depends(resolve_tenant)],
) -> dict:
    tenant, membership = ctx
    # (F-25) Proposing a tenant-scoped catalog model is a write — gate it to
    # owner/manager, matching the product-write guards; sales must not create models.
    _require_owner_or_manager(membership)
    brand = (await db.execute(
        select(CatalogBrand).where(CatalogBrand.id == body.brand_id)
    )).scalar_one_or_none()
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand_not_found")
    cat = (await db.execute(
        select(CatalogCategory).where(CatalogCategory.id == body.category_id)
    )).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "category_not_found")

    slug = _slugify(body.name)
    m = CatalogModel(
        brand_id=body.brand_id, category_id=body.category_id,
        slug=slug, name=body.name.strip(), release_year=body.release_year,
        attributes_template=body.attributes_template or {},
        tenant_id=tenant.id, status="proposed", proposed_by_user_id=membership.user_id,
        is_active=True,
    )
    db.add(m)
    try:
        await db.commit()
    except IntegrityError:
        # This tenant already proposed a model with this slug under this brand —
        # return the existing one so the picker can just select it.
        await db.rollback()
        existing = (await db.execute(
            select(CatalogModel).where(
                CatalogModel.brand_id == body.brand_id,
                CatalogModel.slug == slug,
                CatalogModel.tenant_id == tenant.id,
            )
        )).scalar_one_or_none()
        if existing is not None:
            return _model_dict(existing)
        raise
    await db.refresh(m)
    return _model_dict(m)


@router.patch("/models/{model_id}")
async def update_model(
    model_id: UUID,
    body: ModelIn,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    require_platform_admin(user)
    m = (await db.execute(select(CatalogModel).where(CatalogModel.id == model_id))).scalar_one_or_none()
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "model_not_found")
    for k, v in body.model_dump().items():
        setattr(m, k, v)
    await db.commit()
    await db.refresh(m)
    return _model_dict(m)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: UUID,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    require_platform_admin(user)
    m = (await db.execute(select(CatalogModel).where(CatalogModel.id == model_id))).scalar_one_or_none()
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "model_not_found")
    await db.delete(m)
    await db.commit()


# ── colours taxonomy ─────────────────────────────────────────────────────────

@router.get("/colors")
async def list_colors(db: Annotated[AsyncSession, Depends(get_db)]) -> list[dict]:
    """Public: the global colour palette (uz/ru + hex)."""
    rows = (await db.execute(
        select(CatalogColor).where(CatalogColor.is_active.is_(True))
        .order_by(CatalogColor.sort_order, CatalogColor.name_uz)
    )).scalars().all()
    return [_color_dict(c) for c in rows]
