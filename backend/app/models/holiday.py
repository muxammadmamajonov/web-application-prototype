"""Public holidays — used by the reminder scheduler to skip notifications.

Uzbekistan-specific list seeded by Alembic migration `r18_scheduler_holidays`.
Only the **fixed** set lives in the table; movable Islamic holidays (Ramazon
and Qurbon Hayit) are inserted year-by-year by an upkeep job because their
Gregorian dates shift by ~11 days every solar year.

The reminder cron checks `is_business_day(today)`; if False, the run is
deferred to the next eligible day so customers don't get pinged on a
national holiday. Tenant-specific overrides (e.g. a shop that opens on Eid
mornings) are kept in `tenant_settings.business_hours` and respected
separately by the per-customer template renderer.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

from .base import TimestampMixin, uuid_pk


class Holiday(Base, TimestampMixin):
    __tablename__ = "holidays"

    id: Mapped[uuid_pk]
    # ISO date — single source of truth. We index on `holiday_date` because
    # the cron's first query is "is today a holiday?"
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    name_uz: Mapped[str] = mapped_column(String(120), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(120), nullable=False)
    # "fixed" = same date every year (Independence Day, New Year, etc.)
    # "movable" = lunar — recomputed annually (Ramazon Hayit, Qurbon Hayit)
    kind: Mapped[str] = mapped_column(String(16), default="fixed", nullable=False)
    is_official_day_off: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint("holiday_date", name="uq_holiday_date"),
    )
