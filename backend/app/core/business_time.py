"""Canonical calendar for platform-wide business rules.

Infrastructure timestamps stay UTC. Date-based rules follow the same
Asia/Tashkent calendar used by APScheduler, including the five-hour window in
which the local date is already tomorrow relative to the container clock.

Platform cron legitimately stays hardcoded to :data:`TASHKENT` — a single
scheduler serving every tenant has no per-row zone to read. Per-tenant
*reporting* (MUDD-134/MUDD-140) instead follows the shop's own
``tenants.timezone``, via :func:`resolve_zone` / :func:`tenant_zone` below, so
a tenant outside Uzbekistan is not silently reported in someone else's day.
"""
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TASHKENT = ZoneInfo("Asia/Tashkent")


def _instant(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("business time requires a timezone-aware datetime")
    return current


def tashkent_today(now: datetime | None = None) -> date:
    return _instant(now).astimezone(TASHKENT).date()


def tashkent_day_start_utc(now: datetime | None = None) -> datetime:
    local_date = tashkent_today(now)
    return datetime.combine(local_date, time.min, tzinfo=TASHKENT).astimezone(UTC)


def tashkent_month_start_utc(now: datetime | None = None) -> datetime:
    local_date = tashkent_today(now)
    month_start = local_date.replace(day=1)
    return datetime.combine(month_start, time.min, tzinfo=TASHKENT).astimezone(UTC)


def tashkent_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """The UTC half-open interval ``[start, end)`` covering one Tashkent day.

    Timestamp columns are UTC, so "did this happen yesterday?" is a range test
    against the local day's edges — never ``func.date(ts) == day``, which asks
    the DATABASE's calendar and lands the 00:00–05:00 Tashkent slice on the
    wrong day.

    Both edges are built from local dates rather than by subtracting 24h from
    one of them: that shortcut is only correct while Tashkent has no DST, and
    nothing in this module promises that forever.
    """
    start = datetime.combine(day, time.min, tzinfo=TASHKENT).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=TASHKENT).astimezone(UTC)
    return start, end


# ── per-tenant business day (MUDD-134/MUDD-140) ─────────────────────────────

def resolve_zone(name: str | None) -> ZoneInfo:
    """A validated :class:`ZoneInfo` for a stored timezone string.

    ``tenants.timezone`` is free text, so a blank, stale, or mistyped row must
    degrade to the platform default rather than 500 a report or silently run
    the tenant's day in UTC.
    """
    raw = (name or "").strip()
    if not raw:
        return TASHKENT
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        return TASHKENT


async def tenant_zone(db: object, tenant_id: object) -> ZoneInfo:
    """The validated timezone for one tenant, read fresh (not cached) —
    a report must reflect a timezone change on its very next run.

    Lives here rather than in an API module because both the API layer and
    ``app.services.reports``/``app.services.analytics`` need it, and a service
    importing from ``app.api`` would invert the layering (see
    ``app.core.currency.tenant_primary_currency`` for the same reasoning).
    """
    from sqlalchemy import select

    from app.models import Tenant

    name = (await db.execute(  # type: ignore[attr-defined]
        select(Tenant.timezone).where(Tenant.id == tenant_id)
    )).scalar_one_or_none()
    return resolve_zone(name)


def local_day_bounds_utc(d_from: date, d_to: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Half-open ``[d_from 00:00 local, d_to+1 00:00 local)``, in UTC.

    A plain date range against a ``timestamptz`` column, without truncating
    the boundary day and without going through the session's (UTC) clock.
    """
    start = datetime.combine(d_from, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(d_to + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return start, end
