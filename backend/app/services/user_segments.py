"""Platform-wide user segmentation for admin broadcasting.

A *recipient* is a ``User`` with a Telegram chat (``telegram_id``) who is active.
Segments classify the whole Muddato user base so a platform admin can target a
broadcast at a slice — clients, demo-shop owners, lapsed-trial owners, … . Every
segment yields **distinct telegram_ids**, so a person who is (say) an owner of
one shop AND a customer of another is messaged exactly once.

There is no FK between ``User`` and ``Customer`` — the soft link is equal
``telegram_id`` (a customer who opened the Mini-App). Membership (owner/staff of
a shop) is the ``TenantUser`` row; the shop's lifecycle is ``Tenant.status``.
"""
from __future__ import annotations

from sqlalchemy import ColumnElement, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Customer, Tenant, TenantUser, User
from app.models.enums import TenantRole, TenantStatus

# segment key -> (uz label, ru label). Order = display order.
SEGMENTS: dict[str, tuple[str, str]] = {
    "all":            ("Barcha foydalanuvchilar", "Все пользователи"),
    "clients":        ("Mijozlar (xaridorlar)", "Клиенты (покупатели)"),
    "non_clients":    ("Mijoz bo‘lmaganlar", "Не клиенты"),
    "demo":           ("Demo bizneslar", "Demo-бизнесы"),
    "demo_canceled":  ("Demo tugagan / to‘xtatilgan", "Demo истёк / остановлен"),
    "active_tenants": ("Faol (pullik) bizneslar", "Активные (платные) бизнесы"),
    "owners":         ("Biznes egalari", "Владельцы бизнеса"),
    "staff":          ("Xodimlar (sotuvchi/menejer)", "Сотрудники (продавцы/менеджеры)"),
    "admins":         ("Platforma adminlari", "Админы платформы"),
}

#: Segments that may legitimately be empty are still valid; an UNKNOWN key is not.
VALID_SEGMENTS = frozenset(SEGMENTS)


def _is_customer() -> ColumnElement[bool]:
    """EXISTS a customer row sharing this user's telegram_id."""
    return exists().where(Customer.telegram_id == User.telegram_id)


def _member_of(*tenant_conditions: ColumnElement[bool]) -> ColumnElement[bool]:
    """EXISTS an active membership whose tenant matches ``tenant_conditions``."""
    return exists(
        select(TenantUser.id)
        .join(Tenant, Tenant.id == TenantUser.tenant_id)
        .where(
            TenantUser.user_id == User.id,
            TenantUser.is_active.is_(True),
            *tenant_conditions,
        )
    )


def _role_member(*roles: TenantRole) -> ColumnElement[bool]:
    return exists(
        select(TenantUser.id).where(
            TenantUser.user_id == User.id,
            TenantUser.is_active.is_(True),
            TenantUser.role.in_(roles),
        )
    )


def segment_conditions(segment: str) -> list[ColumnElement[bool]]:
    """The WHERE conditions (on the ``users`` table) that define ``segment``.

    Always combined with the base recipient filter (telegram_id present, active).
    Raises ValueError on an unknown segment.
    """
    if segment not in VALID_SEGMENTS:
        raise ValueError(f"unknown segment: {segment}")
    if segment == "all":
        return []
    if segment == "clients":
        return [_is_customer()]
    if segment == "non_clients":
        return [~_is_customer()]
    if segment == "demo":
        return [_member_of(Tenant.status == TenantStatus.demo)]
    if segment == "demo_canceled":
        # A trial that lapsed: the shop was a trial (trial_ends_at set) and has
        # since been suspended/blocked by the trial-enforcement cron.
        return [_member_of(
            Tenant.status.in_([TenantStatus.suspended, TenantStatus.blocked]),
            Tenant.trial_ends_at.is_not(None),
        )]
    if segment == "active_tenants":
        return [_member_of(Tenant.status == TenantStatus.active)]
    if segment == "owners":
        return [_role_member(TenantRole.owner)]
    if segment == "staff":
        return [_role_member(TenantRole.manager, TenantRole.sales)]
    if segment == "admins":
        return [User.platform_role.is_not(None)]
    return []  # pragma: no cover - exhaustive above


def _base_filter() -> list[ColumnElement[bool]]:
    return [User.telegram_id.is_not(None), User.is_active.is_(True)]


def users_query(segment: str = "all", search: str | None = None):
    """A ``select(User)`` for the segment, optionally name/phone/username-searched."""
    stmt = select(User).where(*_base_filter(), *segment_conditions(segment))
    if search and search.strip():
        like = f"%{search.strip()}%"
        stmt = stmt.where(or_(
            User.first_name.ilike(like),
            User.last_name.ilike(like),
            User.telegram_username.ilike(like),
            # BOTH columns. The canonical one is what an operator will be given
            # to search for; the legacy typed one is still the only number some
            # older rows carry, and dropping it from the search would make those
            # people unfindable by the number support actually has for them.
            User.verified_phone.ilike(like),
            User.phone.ilike(like),
        ))
    return stmt


async def recipient_telegram_ids(db: AsyncSession, segment: str) -> list[int]:
    """Distinct telegram_ids for a segment — the broadcast recipient list."""
    rows = (await db.execute(
        select(User.telegram_id)
        .where(*_base_filter(), *segment_conditions(segment))
        .distinct()
    )).all()
    return [r[0] for r in rows if r[0] is not None]


async def recipients_with_locale(db: AsyncSession, segment: str) -> list[tuple[int, str]]:
    """``(telegram_id, locale)`` per recipient, so a broadcast can be sent in each
    user's own language. ``telegram_id`` is unique per user, so the rows are
    already distinct recipients; locale defaults to ``uz``."""
    rows = (await db.execute(
        select(User.telegram_id, User.locale)
        .where(*_base_filter(), *segment_conditions(segment))
        .distinct()
    )).all()
    return [(r[0], r[1] or "uz") for r in rows if r[0] is not None]


async def segment_counts(db: AsyncSession) -> dict[str, int]:
    """Distinct-recipient count per segment, for the composer's reach preview."""
    out: dict[str, int] = {}
    for key in SEGMENTS:
        out[key] = (await db.execute(
            select(func.count(func.distinct(User.telegram_id)))
            .where(*_base_filter(), *segment_conditions(key))
        )).scalar_one()
    return out
