"""Backup/restore contract after the S3 schedule was retired (P9-E-002).

WHAT CHANGED, AND WHY THIS FILE REPLACES `test_backup_workflow_contract.py`.

`.github/workflows/backup.yml` ran `pg_dump → S3` on a nightly cron. Its required
secrets were never configured, so it failed **every night** and had done so on
every retained run. A permanently-red scheduled workflow is worse than no
workflow: it is a standing signal that operators learn to ignore, and it is
exactly how a genuinely broken job goes unnoticed.

The accepted mechanism for the controlled beta and initial production is an
**encrypted database backup delivered nightly at 01:00 to a private Telegram
channel**, produced and scheduled OUTSIDE this repository. S3 is retired and must
not be reintroduced. Since the workflow's only function was the S3 upload, and a
dispatch-only variant would still demand S3 secrets that must not exist, the
workflow had no remaining valid purpose and was removed rather than disabled.

WHAT IS DELIBERATELY PRESERVED. `backend/scripts/backup_db.sh` and the portable
public-schema restore procedure in `docs/RUNBOOK.md` stay, because they are the
proven manual recovery path and the basis of restore drills — a portable dump
produced by this script has already been restored into a fresh PostgreSQL 17
database. The assertions below that guard that script and that procedure are
carried over from the previous contract test **unchanged in substance**: removing
the workflow must not quietly weaken the recovery contract.

WHAT IS NEW. `test_no_workflow_schedules_an_offsite_database_backup` is the
durable guard that the retired schedule cannot creep back in.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
BACKUP_SCRIPT = REPO / "backend" / "scripts" / "backup_db.sh"
RUNBOOK = REPO / "docs" / "RUNBOOK.md"


def _workflow_files() -> list[Path]:
    files = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
    assert files, "no workflow files found — re-point this test"
    return files


def _triggers(doc: object) -> dict:
    """Return the parsed `on:` mapping for a workflow document.

    YAML 1.1 resolves a bare ``on:`` key to the boolean ``True``, so a naive
    ``doc["on"]`` silently misses every trigger block and would make this guard
    vacuous. Both spellings are accepted.
    """
    if not isinstance(doc, dict):
        return {}
    raw = doc.get("on", doc.get(True))
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return dict.fromkeys(raw)
    if isinstance(raw, str):
        return {raw: None}
    return {}


# ── the retired schedule must not return ────────────────────────────────────

def test_the_s3_backup_workflow_is_retired() -> None:
    assert not (WORKFLOWS / "backup.yml").exists(), (
        "backup.yml is back. The nightly pg_dump → S3 schedule was retired because "
        "its secrets are deliberately absent, so every scheduled run failed and "
        "trained operators to ignore red workflows. Nightly off-site backup is "
        "delivered externally to a private Telegram channel at 01:00."
    )


def test_no_workflow_schedules_an_offsite_database_backup() -> None:
    """No scheduled workflow may perform a database backup or reference S3 backup config.

    This is the durable guard. It is written against BEHAVIOUR (a schedule that
    runs a backup) rather than against the old filename, so re-adding the same
    job under a different name still fails.
    """
    backup_markers = (
        "backup_db.sh",
        "BACKUP_S3_BUCKET",
        "BACKUP_S3_ENDPOINT",
        "pg_dump",
    )
    offenders: list[str] = []
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
        if "schedule" not in _triggers(doc):
            continue  # unscheduled workflows cannot produce a nightly red run
        hit = [m for m in backup_markers if m in text]
        if hit:
            offenders.append(f"{path.name}: scheduled AND references {', '.join(hit)}")

    assert not offenders, (
        "a scheduled workflow performs an off-site database backup again:\n"
        + "\n".join(offenders)
        + "\n\nThe accepted mechanism is an encrypted dump delivered to a private "
        "Telegram channel at 01:00, scheduled outside this repository. Do not "
        "reintroduce S3. A manual/dispatch-only recovery path is fine — the "
        "schedule is what this guard forbids."
    )


def test_no_workflow_carries_s3_backup_credentials() -> None:
    """S3 is retired; no workflow should ask for its credentials at all."""
    offenders = [
        p.name for p in _workflow_files()
        if "BACKUP_S3_BUCKET" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"S3 backup configuration reintroduced in: {offenders}"


# ── the manual recovery path must remain intact (carried over unchanged) ─────

def test_backup_script_still_produces_a_portable_public_schema_dump() -> None:
    """Preserved from the retired workflow's contract.

    The application owns only the `public` schema. A whole-Supabase-project dump
    includes provider-managed extensions and schemas that cannot be restored into
    an ordinary PostgreSQL target — which is precisely what makes the accepted
    Telegram-delivered backup restorable.
    """
    assert BACKUP_SCRIPT.is_file(), "backup_db.sh is the manual recovery path; it must not be deleted"
    script = BACKUP_SCRIPT.read_text(encoding="utf-8")
    assert 'dump_bin="${PG_DUMP_BIN:-pg_dump}"' in script
    assert '"$dump_bin" --format=custom' in script
    assert "--schema=public" in script


def test_backup_script_runs_without_s3_configured() -> None:
    """With S3 retired, the ordinary path is a local dump the operator collects.

    The script must therefore still succeed when `BACKUP_S3_BUCKET` is unset,
    rather than treating the absent bucket as an error.
    """
    script = BACKUP_SCRIPT.read_text(encoding="utf-8")
    assert 'if [[ -n "${BACKUP_S3_BUCKET:-}" ]]; then' in script
    assert "else" in script.split('if [[ -n "${BACKUP_S3_BUCKET:-}" ]]; then', 1)[1]


def test_restore_runbook_prepares_a_fresh_postgres_for_the_public_schema_dump() -> None:
    """Preserved verbatim from the retired workflow's contract."""
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in runbook
    assert "DROP SCHEMA IF EXISTS public CASCADE" in runbook


def test_runbook_states_the_accepted_backup_mechanism() -> None:
    """Documentation must describe reality, not the retired schedule."""
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "01:00" in runbook, "the runbook must state the accepted nightly backup time"
    assert "Telegram" in runbook, "the runbook must name the accepted delivery channel"


def test_runbook_does_not_present_the_retired_workflow_as_live() -> None:
    """The runbook may EXPLAIN the retirement; it must not instruct anyone to use it.

    A bare substring ban on the filename was the first attempt and was wrong: it
    forbids the very sentence that tells a future operator why the workflow is
    gone. What actually matters is that no line presents it as an active
    mechanism, and that the S3 activation instructions are gone.
    """
    runbook = RUNBOOK.read_text(encoding="utf-8")

    for line_no, line in enumerate(runbook.splitlines(), start=1):
        if "workflows/backup.yml" in line:
            assert "retired" in line.lower(), (
                f"docs/RUNBOOK.md:{line_no} mentions the backup workflow without saying "
                "it is retired, which reads as a live instruction:\n  " + line.strip()
            )

    # The old activation recipe must not survive anywhere in the runbook.
    for phrase in ("BACKUP_S3_BUCKET", "AWS_SECRET_ACCESS_KEY", "s3://muddato-backups"):
        assert phrase not in runbook, (
            f"the runbook still carries the retired S3 activation instructions ({phrase})"
        )
