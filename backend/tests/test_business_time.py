"""Date-based business rules follow the scheduler's Asia/Tashkent clock."""
from datetime import UTC, datetime
from pathlib import Path

from app.core.business_time import tashkent_day_start_utc, tashkent_today


def test_tashkent_new_year_is_not_yesterday_in_utc() -> None:
    instant = datetime(2025, 12, 31, 19, 30, tzinfo=UTC)  # 2026-01-01 00:30 UZT
    assert tashkent_today(instant).isoformat() == "2026-01-01"
    assert tashkent_day_start_utc(instant) == datetime(2025, 12, 31, 19, 0, tzinfo=UTC)


def test_date_based_scheduler_jobs_do_not_read_the_container_calendar() -> None:
    root = Path(__file__).parents[1]
    scheduler = (root / "app/services/scheduler.py").read_text()
    billing = (root / "app/services/billing.py").read_text()
    assert "date.today()" not in scheduler
    assert "today = today or date.today()" not in billing
