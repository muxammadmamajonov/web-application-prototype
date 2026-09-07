"""Production synthetic monitoring must exercise the live MVP entry points."""
from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "synthetic-monitor.yml"


def _workflow() -> str:
    assert WORKFLOW.is_file()
    return WORKFLOW.read_text(encoding="utf-8")


def test_monitor_runs_on_a_schedule_and_fails_closed_on_live_checks() -> None:
    text = _workflow()
    assert "schedule:" in text
    assert "workflow_dispatch:" in text

    # The cadence is pinned as an INVARIANT, not a literal.
    #
    # This asserted `cron: "*/10 * * * *"` verbatim. That literal was the thing
    # that needed changing: on a private repo every job is billed rounded up to a
    # whole minute, so 144 runs/day ≈ 4,320 min/month against a 2,000-minute
    # allowance. The account went over, Actions stopped starting ANY job, and
    # because deploy.yml gates on "CI green on main", production deploys silently
    # stopped too. A test pinning the literal would have had to be edited to fix
    # that outage — which is a test defending the bug.
    #
    # What actually matters is bounded: there IS a schedule, and it does not run
    # more often than hourly. Anything finer belongs on a platform that does not
    # consume CI minutes (Cloudflare Health Checks) — see the comment on the cron
    # itself and docs/HARDENING_AUDIT_2026-08-12.md.
    import re

    crons = re.findall(r'cron:\s*"([^"]+)"', text)
    assert crons, "the monitor must still run on a schedule"
    for expr in crons:
        minute = expr.split()[0]
        assert not minute.startswith("*/"), (
            f"cron {expr!r} runs sub-hourly. On a private repo that is billed per "
            "started job and takes CI down with it when the allowance runs out."
        )
    for endpoint in (
        "https://muddato.uz/",
        "https://muddato.pages.dev/",
        "https://muddato-api.fly.dev/api/plans/public",
        "https://muddato-api.fly.dev/health",
        "https://muddato-api.fly.dev/ready",
        "https://muddato-api.fly.dev/api/bot/webhook",
    ):
        assert endpoint in text
    assert "--fail-with-body" in text
    assert "--retry 2" in text
    assert "dig +short A muddato.uz" in text
    assert "domain_status" in text
    assert "200|403)" in text
    assert 'test "$bot_status" = "401"' in text
    assert '.detail == "bad_secret"' in text
    assert "continue-on-error" not in text


def test_monitor_opens_one_assigned_incident_and_closes_it_after_recovery() -> None:
    text = _workflow()
    assert "issues: write" in text
    assert "if: failure()" in text
    assert "if: success()" in text
    assert "[Synthetic Monitor] Muddato production check failed" in text
    assert "gh issue create" in text
    assert '--assignee "$GITHUB_REPOSITORY_OWNER"' in text
    assert "gh issue comment" in text
    assert "gh issue close" in text
    assert "GH_TOKEN: ${{ github.token }}" in text


def test_monitor_has_a_controlled_alert_delivery_drill() -> None:
    text = _workflow()
    assert "drill_failure:" in text
    assert "type: boolean" in text
    assert "default: false" in text
    assert "if: ${{ inputs.drill_failure }}" in text
    assert "Controlled alert drill" in text
    assert "exit 1" in text
