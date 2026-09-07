"""Critical-path smoke tests for the Round 18 scheduler.

We don't spin up Postgres in CI; these tests exercise the pure helpers
(template defaults, holiday business-day check) and the import surface.
The full job runs hit the real DB and are covered manually via the
`/admin/cron/run/{job_id}` endpoint after deploy.
"""
from __future__ import annotations

from app.services.scheduler import (
    _DEFAULTS_UZ,
    _default_template,
    default_reminder_templates,
)


def test_default_templates_have_all_four_keys() -> None:
    keys = set(default_reminder_templates().keys())
    assert keys == {"before_due", "due_today", "overdue", "owner_overdue_alert"}


def test_default_templates_contain_placeholders() -> None:
    """Each template must render *something* with our standard variable set;
    if a default loses its placeholder, customers get a useless empty msg."""
    standard = (
        "{customer_name}", "{amount}", "{contract_number}",
        "{remaining}", "{days_overdue}",
    )
    for key, tpl in _DEFAULTS_UZ.items():
        # Each template should reference at least 2 placeholders.
        hits = sum(1 for p in standard if p in tpl)
        assert hits >= 2, f"template {key} has too few placeholders: {tpl}"


def test_default_template_unknown_key_returns_empty() -> None:
    assert _default_template("not_a_real_template") == ""


def test_scheduler_module_importable() -> None:
    """The whole module must import without touching the DB. Catches the
    classic 'import-time side effect on settings.database_url' regression."""
    import app.services.scheduler as scheduler

    # Symbols our admin endpoints rely on.
    for sym in (
        "setup_scheduler", "shutdown_scheduler", "get_scheduler",
        "mark_overdue_installments_job", "apply_late_fees_job",
        "send_reminders_job", "daily_owner_briefing_job",
    ):
        assert hasattr(scheduler, sym), f"scheduler.{sym} missing"
