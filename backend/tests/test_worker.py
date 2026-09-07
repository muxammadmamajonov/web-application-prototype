"""#82 — dedicated scheduler worker process + RUN_SCHEDULER gating."""
from __future__ import annotations

import pytest


def test_run_scheduler_defaults_true() -> None:
    from app.config import settings

    assert settings.run_scheduler is True


def test_run_scheduler_env_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUN_SCHEDULER", "false")
    from app.config import Settings

    assert Settings().run_scheduler is False  # type: ignore[call-arg]


async def test_worker_boot_starts_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.worker as worker

    calls = {"setup": 0, "billing": 0}

    async def _fake_setup() -> None:
        calls["setup"] += 1

    async def _fake_billing() -> dict:
        calls["billing"] += 1
        return {"issued": 0}

    monkeypatch.setattr(worker, "setup_scheduler", _fake_setup)
    monkeypatch.setattr(worker, "run_billing_cycle_job", _fake_billing)
    monkeypatch.setattr(worker, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr(worker, "init_sentry", lambda: None)

    await worker._boot()
    assert calls["setup"] == 1
    assert calls["billing"] == 1
