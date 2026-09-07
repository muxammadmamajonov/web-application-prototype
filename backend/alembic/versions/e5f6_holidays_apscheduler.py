"""holidays table + APScheduler jobstore + seed UZ 2026-2027

Revision ID: e5f6holidays
Revises: d4e5pending
Create Date: 2026-05-29 00:00:00+00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "e5f6holidays"
down_revision = "d4e5pending"
branch_labels = None
depends_on = None


# ── UZ official holidays — sourced from the State Committee on Statistics
# annual decree. Includes only the days designated as official non-working
# in the Labour Code (Articles 132-133). Movable Islamic dates are inserted
# year-by-year by the scheduler's `refresh_islamic_holidays` job; the seed
# below pre-populates 2026 + 2027 calculated values to bootstrap the table.
UZ_HOLIDAYS_SEED = [
    # ── 2026 ─────────────────────────────────────────────────────────
    ("2026-01-01", "Yangi yil", "Новый год", "fixed"),
    ("2026-03-08", "Xotin-qizlar kuni", "Международный женский день", "fixed"),
    ("2026-03-21", "Navro'z bayrami", "Праздник Навруз", "fixed"),
    ("2026-05-09", "Xotira va qadrlash kuni", "День памяти и почестей", "fixed"),
    ("2026-09-01", "Mustaqillik kuni", "День независимости", "fixed"),
    ("2026-10-01", "O'qituvchi va murabbiylar kuni", "День учителя и наставника", "fixed"),
    ("2026-12-08", "Konstitutsiya kuni", "День Конституции", "fixed"),
    # Ramazon Hayit 2026: 1 Shawwal 1447 AH = approx 2026-03-20 (Astronomical
    # New Moon). UzNews observation may shift ±1 day; refresh job corrects.
    ("2026-03-20", "Ramazon hayit", "Рамазан хайит", "movable"),
    # Qurbon Hayit 2026: 10 Dhu al-Hijjah 1447 AH = approx 2026-05-27.
    ("2026-05-27", "Qurbon hayit", "Курбан хайит", "movable"),

    # ── 2027 ─────────────────────────────────────────────────────────
    ("2027-01-01", "Yangi yil", "Новый год", "fixed"),
    ("2027-03-08", "Xotin-qizlar kuni", "Международный женский день", "fixed"),
    ("2027-03-21", "Navro'z bayrami", "Праздник Навруз", "fixed"),
    ("2027-05-09", "Xotira va qadrlash kuni", "День памяти и почестей", "fixed"),
    ("2027-09-01", "Mustaqillik kuni", "День независимости", "fixed"),
    ("2027-10-01", "O'qituvchi va murabbiylar kuni", "День учителя и наставника", "fixed"),
    ("2027-12-08", "Konstitutsiya kuni", "День Конституции", "fixed"),
    ("2027-03-09", "Ramazon hayit", "Рамазан хайит", "movable"),
    ("2027-05-17", "Qurbon hayit", "Курбан хайит", "movable"),
]


def upgrade() -> None:
    # ── holidays ─────────────────────────────────────────────────────
    op.create_table(
        "holidays",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name_uz", sa.String(length=120), nullable=False),
        sa.Column("name_ru", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="fixed"),
        sa.Column("is_official_day_off", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("holiday_date", name="uq_holiday_date"),
    )
    op.create_index("ix_holidays_holiday_date", "holidays", ["holiday_date"], unique=False)

    # Seed UZ official holidays for 2026 + 2027. Conflict on date is a no-op
    # so re-running the migration on a fresh DB stays idempotent.
    for d, uz, ru, kind in UZ_HOLIDAYS_SEED:
        op.execute(
            f"""
            INSERT INTO holidays (holiday_date, name_uz, name_ru, kind, is_official_day_off)
            VALUES ('{d}', '{uz.replace("'", "''")}', '{ru.replace("'", "''")}', '{kind}', true)
            ON CONFLICT (holiday_date) DO NOTHING
            """
        )

    # ── APScheduler jobstore (apscheduler_jobs) ──────────────────────
    # APScheduler manages this table itself via SQLAlchemyJobStore and
    # auto-creates it on first boot. The scheduler may have already run
    # against the DB before this migration applies, so we create the
    # table + index idempotently with IF NOT EXISTS to avoid colliding
    # with APScheduler's own DDL (relation already exists errors).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS apscheduler_jobs (
            id VARCHAR(191) NOT NULL,
            next_run_time DOUBLE PRECISION,
            job_state BYTEA NOT NULL,
            PRIMARY KEY (id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_apscheduler_jobs_next_run_time "
        "ON apscheduler_jobs (next_run_time)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_apscheduler_jobs_next_run_time")
    op.execute("DROP TABLE IF EXISTS apscheduler_jobs")
    op.drop_index("ix_holidays_holiday_date", table_name="holidays")
    op.drop_table("holidays")
