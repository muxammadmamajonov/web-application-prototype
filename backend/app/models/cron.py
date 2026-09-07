"""Cron lease lock (R60).

APScheduler runs in-process and does no cross-instance coordination, so during a
deploy (old + new worker briefly coexisting) or any accidental second worker a
job could fire twice — duplicate reminders, double late fees. Each job claims a
TTL'd row here via an atomic upsert before running (``services/scheduler``).

The table is created by migration ``r60_cron_locks``; this model exists so the
ORM and a future migration agree on the schema and so ``Base.metadata`` (used by
the model-only test fixtures) knows the table. The lock is still driven by raw
SQL in the scheduler — a single pooler-safe upsert, no session/ORM state — so
this class is intentionally minimal.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CronLock(Base):
    __tablename__ = "cron_locks"

    job_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The lease expiry. A job holds the lock until this instant; the upsert only
    # reclaims a row whose ``locked_until < now()`` so a crashed holder self-heals
    # after the TTL. (F-09) Release no longer pulls this back to now() early, and
    # the TTL is sized above worst-case job runtime, so an overrunning job keeps
    # its lock for the whole run instead of losing it mid-flight.
    locked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
