"""The production Fly.io deployment must not drift below launch capacity.

An idle deployment billed $25.91 for July 2026: three `shared-cpu-2x` machines
with 1 GB each, running around the clock in `nrt`, serving nobody. Two settings
caused it, and neither is obvious from reading the app:

  * `min_machines_running = 2` — the Fly proxy keeps that many machines started
    at all times and restarts any machine stopped by hand, so "just stop it"
    never held.
  * a `worker` process group with no `[http_service]` attached — nothing can
    auto-stop a machine that serves no HTTP, so it ran 24/7 by construction.

Scale counts alone do not survive: `fly deploy` recreates every process group
listed in `[processes]`, which is exactly how a `fly scale count worker=0`
was silently undone by the next deploy. Only removing the entry makes it stick.

**Production posture (2026-08-09): two warm 1 GB app machines plus one worker.**
The product now carries financial contracts and PDF rendering, so availability
and OOM headroom take priority over the former pre-launch zero-idle-cost posture.

It reads the committed `fly.toml`, which is what `deploy.yml` ships
(`working-directory: backend`), so it guards the file that actually reaches
production rather than a copy of it.

WHEN CHANGING THE POSTURE: it is a deliberate act either way. Flip the
expectations here in the same commit that flips `fly.toml`, so the cost change
is reviewed rather than absorbed. See `docs/RUNBOOK.md` § 4.0.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

FLY_TOML = Path(__file__).resolve().parents[1] / "fly.toml"

LAUNCH = (
    "If this is a deliberate launch change, update this test in the same commit "
    "(see docs/RUNBOOK.md § pre-launch cost posture)."
)


@pytest.fixture(scope="module")
def fly() -> dict:
    assert FLY_TOML.is_file(), f"{FLY_TOML} is missing — deploy.yml ships this file"
    return tomllib.loads(FLY_TOML.read_text())


def test_two_app_machines_stay_warm_for_ha(fly):
    svc = fly["http_service"]
    assert svc["min_machines_running"] == 2, LAUNCH


def test_machines_suspend_and_restart_on_demand(fly):
    svc = fly["http_service"]
    assert svc["auto_start_machines"] is True, (
        f"with auto_start off, a scaled-to-zero app is simply down. {LAUNCH}"
    )
    assert svc["auto_stop_machines"] == "suspend", (
        f"auto_stop_machines={svc['auto_stop_machines']!r} — expected 'suspend'. {LAUNCH}"
    )


def test_machine_has_pdf_rendering_headroom(fly):
    vm = fly["vm"][0]
    assert vm["size"] == "shared-cpu-2x", f"size={vm['size']!r}. {LAUNCH}"
    assert vm["memory"] == "1024mb", f"memory={vm['memory']!r}. {LAUNCH}"


#: Process groups allowed to run continuously, each with the reason it must.
#:
#: A group in `[processes]` but not in `[http_service].processes` has no service
#: attached, so fly-proxy can never auto-stop it: it runs 24/7 by construction.
#: That is sometimes correct — but it should be a decision someone made on
#: purpose, not something that appears in a diff nobody priced.
ALWAYS_ON_BY_DESIGN = {
    # Restored 2026-08-05 for launch. APScheduler: payment reminders, dunning,
    # mark_overdue, daily owner briefing. Without it those stop silently while
    # the API keeps answering, so a shop's contracts never go overdue and no
    # customer is ever chased. Costs one small machine running around the clock.
    "worker",
}


def test_only_known_process_groups_run_continuously(fly):
    """Nothing may quietly acquire a 24/7 machine.

    Deliberately an allowlist rather than a check for the name `worker`: the
    July 2026 bill came from exactly this shape, and a future `beat`, `consumer`
    or `queue` group would reproduce it under a different name and sail through
    a check that only knew about `worker`.

    Adding a group here is the point — it forces the cost into review.
    """
    declared = set(fly["processes"])
    stoppable = set(fly["http_service"]["processes"])
    always_on = declared - stoppable

    unreviewed = always_on - ALWAYS_ON_BY_DESIGN
    assert not unreviewed, (
        f"process group(s) {sorted(unreviewed)} are declared but not attached to "
        f"[http_service], so nothing can ever stop them and they bill 24/7. If "
        f"that is intended, add each to ALWAYS_ON_BY_DESIGN with the reason it "
        f"must run continuously. {LAUNCH}"
    )


def test_the_allowlist_does_not_outlive_the_groups_it_describes(fly):
    """A stale allowlist entry silently re-permits a group someone removed.

    If `worker` is commented out again for a quiet period, this fails until the
    allowlist is updated too — so the list stays a statement about the current
    deployment rather than an accumulating record of everything ever allowed.
    """
    declared = set(fly["processes"])
    stale = ALWAYS_ON_BY_DESIGN - declared
    assert not stale, (
        f"ALWAYS_ON_BY_DESIGN still lists {sorted(stale)}, which is no longer "
        f"declared in [processes]. Remove the entry so the allowlist keeps "
        f"describing what actually ships."
    )


def test_the_scheduler_is_deployable_at_launch(fly):
    """The scheduler must be declared, or nothing runs the money-path cron.

    Note this asserts the group is DECLARED, not that a machine exists —
    `fly deploy` creates the group, but the running count comes from
    `fly scale count worker=1`, which no file in this repo can guarantee.
    Verify with `fly scale show -a muddato-api`.
    """
    assert "worker" in fly["processes"], (
        "the `worker` process is not declared — payment reminders, dunning, "
        "mark_overdue and the daily owner briefing will not run, silently."
    )
