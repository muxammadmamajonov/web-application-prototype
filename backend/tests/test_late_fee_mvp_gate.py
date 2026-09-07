"""Late fees are Phase-2 LATER and must be inert in the MVP."""
from __future__ import annotations


async def test_late_fee_job_is_disabled_by_default(monkeypatch) -> None:
    from app.config import settings
    from app.services import scheduler

    assert settings.enable_late_fees is False

    class _MustNotOpen:
        def __call__(self):
            raise AssertionError("disabled late-fee job touched the database")

    monkeypatch.setattr(scheduler, "SessionLocal", _MustNotOpen())
    result = await scheduler.apply_late_fees_job()
    assert result == {"skipped": True, "reason": "feature_disabled"}
