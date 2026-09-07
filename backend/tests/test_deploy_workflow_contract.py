"""Production deploy workflow must fail fast, fail loud, and never leak a secret.

WHAT THIS PINS (P9-E-001, 2026-08-09).

`Deploy (production)` runs two independent jobs: backend → Fly, frontend →
Cloudflare Pages. On run 31313196369 the backend job **succeeded** while the
frontend job **failed**, because `CLOUDFLARE_API_TOKEN` is not configured.

The failure itself was correct. Its *shape* was not: the frontend job checked
out, installed pnpm and Node, ran `pnpm install --frozen-lockfile` and a full
production `pnpm build`, and only then — 59 s in, at the last step — did
`wrangler` refuse with its generic "In a non-interactive environment…" message.
That reads as a build or tooling problem rather than an unconfigured repository
secret, and it pays for a full build to learn it.

A preflight now runs first, names the exact missing secret, and states plainly
that the frontend was NOT deployed. The token itself cannot be fixed in code —
it is repository configuration — so the finding's disposition is VERIFIED
EXTERNAL CONFIGURATION REQUIRED, and these tests guard the code-side half.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml"


def _text() -> str:
    assert WORKFLOW.is_file()
    return WORKFLOW.read_text(encoding="utf-8")


def _doc() -> dict:
    return yaml.safe_load(_text())


def _job(name: str) -> dict:
    jobs = _doc()["jobs"]
    assert name in jobs, f"job '{name}' missing from deploy.yml — re-point this test"
    return jobs[name]


def _needs(job: dict) -> set[str]:
    needs = job.get("needs")
    if needs is None:
        return set()
    return {needs} if isinstance(needs, str) else set(needs)


def test_backend_and_frontend_deploy_as_independent_jobs() -> None:
    """Each tier must be separately observable.

    A single combined job would let a frontend failure hide behind a green
    backend, or mask which half of production actually shipped. Neither may
    depend on the OTHER — that is the property that made the split diagnosis
    on run 31313196369 possible. Both may depend on the shared `guard` job
    (MUDD-154, the tip-of-main check): that is a common precondition applied
    identically to both tiers, not one tier depending on the other.
    """
    jobs = _doc()["jobs"]
    assert {"backend", "frontend"} <= set(jobs), jobs.keys()
    assert "frontend" not in _needs(jobs["backend"])
    assert "backend" not in _needs(jobs["frontend"])


def test_frontend_preflights_cloudflare_secrets_before_installing_or_building() -> None:
    """The secret check must come before the expensive steps, or it is not a preflight."""
    steps = _job("frontend")["steps"]
    names = [str(s.get("name", s.get("uses", ""))) for s in steps]

    preflight = next(
        (i for i, s in enumerate(steps)
         if "CLOUDFLARE_API_TOKEN" in str(s.get("env", {}))
         and "test -n" in str(s.get("run", ""))),
        None,
    )
    assert preflight is not None, (
        "the frontend job has no secret preflight; a missing CLOUDFLARE_API_TOKEN "
        "would again only surface at the final wrangler step, after a full build"
    )

    def index_of(pattern: str) -> int:
        for i, n in enumerate(names):
            if pattern.lower() in n.lower():
                return i
        for i, s in enumerate(steps):
            if pattern.lower() in str(s.get("run", "")).lower():
                return i
        raise AssertionError(f"step matching {pattern!r} not found — re-point this test")

    for later in ("checkout", "pnpm install", "pnpm build", "wrangler pages deploy"):
        assert preflight < index_of(later), (
            f"the preflight runs after '{later}'; it must be first so the job fails "
            "in seconds instead of after an install and a production build"
        )


def test_preflight_checks_both_required_cloudflare_secrets() -> None:
    run = " ".join(
        str(s.get("run", "")) for s in _job("frontend")["steps"]
        if "CLOUDFLARE_API_TOKEN" in str(s.get("env", {}))
    )
    for secret in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):
        assert f'test -n "${secret}"' in run, f"preflight does not verify {secret}"


def test_the_failure_message_names_the_secret_and_says_the_frontend_did_not_deploy() -> None:
    """An operator must learn the cause without opening the raw log."""
    text = _text()
    assert "CLOUDFLARE_API_TOKEN is not configured" in text
    assert "NOT deployed" in text


def test_no_secret_value_is_ever_echoed() -> None:
    """Secrets may be injected via `env:`; they may never be printed.

    `test -n "$VAR"` is safe. `echo "$VAR"`, or interpolating `${{ secrets.X }}`
    straight into a shell command, would put the value in a world-readable log.
    """
    text = _text()
    # No secret expression may appear inside a `run:` shell body.
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "secrets." in stripped and ("echo" in stripped or "printf" in stripped):
            raise AssertionError(f"a secret may be printed to the log: {stripped[:100]}")
    # Secrets must be bound through `env:`, never expanded inline in a command.
    assert not re.search(r"run:.*\$\{\{\s*secrets\.", text), (
        "a secret is interpolated directly into a run: command; bind it via env: instead"
    )


def test_automatic_deploy_still_requires_a_green_ci_run() -> None:
    """The F-06 gate must survive edits to this file.

    MUDD-154 centralised this condition into the `guard` job, alongside the
    new tip-of-main check, rather than duplicating the raw condition on both
    tiers. `backend`/`frontend` therefore gate on `guard`'s output instead of
    repeating `conclusion == 'success'` themselves — but they cannot run
    unless `guard` both saw a green CI run (or a manual dispatch) AND found
    the commit still current.
    """
    text = _text()
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "conclusion == 'success'" in str(_job("guard")["if"])
    for job in ("backend", "frontend"):
        assert "guard" in _needs(_job(job))
        assert "should_deploy" in str(_job(job)["if"])


def test_workflow_run_is_branch_filtered_to_main() -> None:
    """Pins the mitigation that makes the `workflow_run` + checkout pattern safe.

    Static scanners flag `workflow_run` workflows that check out
    `workflow_run.head_sha` as a potential pwn-request: a fork PR's code could be
    built in a context that holds repository secrets. Here that path is closed by
    `branches: [main]` — CI runs for a fork PR have the PR's head branch, not
    `main`, so they never trigger this workflow — combined with the
    `conclusion == 'success'` gate above.

    That mitigation is a single line and easy to delete by accident, so it is
    pinned here. (Removing it would make the scanner finding real.)
    """
    doc = _doc()
    triggers = doc.get("on", doc.get(True))  # YAML 1.1 resolves bare `on:` to True
    assert triggers["workflow_run"]["branches"] == ["main"]


def test_a_failed_deploy_raises_an_incident_instead_of_a_silent_red_run() -> None:
    """2026-08-26: the frontend job died at `wrangler pages deploy` with
    `fetch failed` while the backend job had already succeeded. Production ran
    SEVEN HOURS on a new API with the previous frontend, and nobody knew — the
    only signal was the run's own status, which nobody was watching.

    The notifier must cover *any* job failing, not just "both failed", because
    the half-deployed split is the case that most needs saying out loud.
    """
    import yaml

    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    notify = jobs.get("notify-failure")
    assert notify is not None, "a failed deploy has no reporting path at all"

    assert str(notify.get("if")).strip() == "failure()", (
        "the notifier must fire on ANY job failure. A stricter condition would "
        "skip the half-deployed split — one tier live, one not — which is the "
        "most dangerous outcome, not the least."
    )
    assert set(notify.get("needs") or []) == {"guard", "backend", "frontend"}, (
        "the notifier must depend on every upstream job. `guard` (MUDD-154) is "
        "included so a genuine failure to resolve the tip of main is reported "
        "like any other deploy failure — a stale-SHA skip is NOT a failure "
        "(guard exits 0 for that path) and so never reaches this assertion's "
        "concern; dropping backend or frontend would mean a failure in the "
        "tier this job does not watch reports nothing"
    )
    assert notify.get("permissions", {}).get("issues") == "write", (
        "opening the incident needs issues: write; the workflow default is "
        "contents: read only"
    )
    assert "timeout-minutes" in notify, (
        "an unbounded notifier can hold a self-hosted runner — the exact class of "
        "wedge that cost 9 hours on 2026-08-26 (see test_ci_workflow_contract)"
    )


def test_the_incident_body_reports_each_tier_separately() -> None:
    """"Deploy failed" is not actionable. Which half shipped decides whether the
    next move is a re-run or a rollback."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "needs.backend.result" in text and "needs.frontend.result" in text, (
        "the incident must state each tier's result; without them the reader "
        "cannot tell a total failure from a split deploy"
    )
    assert "split" in text.lower(), (
        "the incident should name the split-deploy hazard explicitly — it is not "
        "obvious from two result strings alone"
    )


def test_every_deploy_job_is_time_bounded() -> None:
    """An unbounded job on this host is a runner slot with no way back.

    The five self-hosted runners share one Mac mini. ci.yml caps every job for
    that reason; deploy.yml capped only `notify-failure`, so the two jobs that
    actually reach the network -- `flyctl deploy --remote-only` and `npx
    wrangler pages deploy` -- could hang indefinitely and starve every queued
    CI job behind them. That is not hypothetical on this host: an unbounded
    network-bound step held a runner for nine hours on 2026-08-26 and cancelled
    the production synthetic monitor three times while it did.

    The VALUES are free to tune; their existence is not.
    """
    for name, job in _doc()["jobs"].items():
        assert isinstance(job.get("timeout-minutes"), int), (
            f"deploy job `{name}` has no `timeout-minutes` — a hung step would "
            "hold one of the five shared runner slots with nothing to stop it"
        )


def test_each_tier_proves_production_serves_the_new_build() -> None:
    """"The deploy command exited 0" and "users have the new code" differ.

    Both halves of this have already cost us production time. `flyctl deploy`
    can report success for a release that then cannot reach Postgres, and on
    2026-08-26 `wrangler pages deploy` succeeded while muddato.uz kept serving
    the PREVIOUS frontend for seven hours -- every square green the whole time.
    Each was eventually caught by a human running curl afterwards, which is not
    a control: it is a habit, and habits get skipped.

    So each tier must end by asserting against production itself. The backend
    probes `/ready` (which reports `db` and `redis`, unlike `/health`, so a
    booted-but-disconnected container fails here). The frontend compares the
    hashed entry bundle it just built against what muddato.uz actually serves.
    """
    backend_steps = _job("backend")["steps"]
    # Match the URL that is actually requested, not the word "/ready" — the
    # step echoes that word in its own progress and error messages, and a
    # mutation that repointed the probe at `/health` kept every one of those
    # strings while silently dropping the dependency check. Prose must not be
    # able to satisfy a behavioural assertion.
    verify = [
        s for s in backend_steps
        if re.search(r"curl[^\n]*muddato-api\.fly\.dev/ready", str(s.get("run", "")))
    ]
    assert verify, (
        "the backend job never requests production /ready — `flyctl deploy` "
        "exiting 0 is not evidence the API serves requests, and /health alone "
        "cannot tell a healthy API from one that cannot reach Postgres"
    )
    probe = str(verify[-1]["run"])
    assert '"db":"ok"' in probe and '"redis":"ok"' in probe, (
        "the backend verification accepts /ready without checking `db` and "
        "`redis`; a container that booted but cannot reach Postgres would pass"
    )
    assert backend_steps.index(verify[-1]) > next(
        i for i, s in enumerate(backend_steps) if "flyctl deploy" in str(s.get("run", ""))
    ), "the backend verification runs BEFORE the deploy — it would check the old release"

    frontend_steps = _job("frontend")["steps"]
    # Same reason as above: the step names muddato.uz in its own error message,
    # so require the actual request, not a mention of the host.
    checks = [
        s for s in frontend_steps
        if re.search(r"curl[^\n]*muddato\.uz", str(s.get("run", "")))
    ]
    assert checks, (
        "the frontend job never checks what muddato.uz actually serves — a "
        "successful upload does not mean the custom domain points at it"
    )
    check = str(checks[-1]["run"])
    assert "dist/index.html" in check, (
        "the frontend verification does not read the hash it just built from "
        "dist/index.html, so it cannot tell THIS build from any other"
    )
    assert frontend_steps.index(checks[-1]) > next(
        i for i, s in enumerate(frontend_steps) if "wrangler pages deploy" in str(s.get("run", ""))
    ), "the frontend verification runs BEFORE the deploy — it would pass on the old build"


def test_production_verification_cannot_be_softened_into_a_warning() -> None:
    """A check that cannot fail the job is a log line, not a gate.

    `continue-on-error` on either verification would restore precisely the
    state this replaced: a green run that says nothing about production. The
    SBOM attestation step taught this once already (#991) -- it reported
    success for months while persisting nothing.
    """
    for job_name in ("backend", "frontend"):
        for step in _job(job_name)["steps"]:
            run = str(step.get("run", ""))
            if not re.search(
                r"curl[^\n]*(muddato-api\.fly\.dev/ready|muddato\.uz)", run
            ):
                continue
            assert not step.get("continue-on-error"), (
                f"`{job_name}`'s production verification carries "
                "continue-on-error — it would report success no matter what "
                "production is serving"
            )


# ---------------------------------------------------------------------------
# MUDD-154: `workflow_run.head_sha` is whatever commit the TRIGGERING CI run
# validated, not necessarily the newest commit on `main` by the time this
# workflow runs. ci.yml's concurrency group is keyed per commit for push
# events (MUDD-150, deliberately, so back-to-back merges don't cancel each
# other's CI) — so two CI runs for two different `main` commits can be
# in_progress at once, and `deploy-production`'s `cancel-in-progress: false`
# only serialises the resulting deploy runs, it does not order them by commit
# recency. Without a guard, the CI run for an OLDER commit finishing after a
# NEWER one's would deploy the older commit second and silently roll
# production back, every gate green.
# ---------------------------------------------------------------------------


def test_guard_resolves_the_tip_of_main_before_either_deploy_job() -> None:
    """The stale-SHA check must run before checkout, and gate both tiers."""
    guard = _job("guard")
    assert isinstance(guard.get("timeout-minutes"), int)
    assert "should_deploy" in guard.get("outputs", {}), (
        "guard produces no should_deploy output — backend/frontend have "
        "nothing to gate on"
    )
    steps_text = " ".join(str(s.get("run", "")) for s in guard["steps"])
    # Resolved via the API, not by checking out the repo — the whole point is
    # to learn the answer BEFORE paying for (or risking) a checkout.
    assert "commits/main" in steps_text
    assert "checkout" not in " ".join(
        str(s.get("uses", "")) for s in guard["steps"]
    ).lower()


def test_guard_skips_without_failing_on_a_stale_commit() -> None:
    """A stale commit must be SKIPPED, never FAILED — MUDD-154's whole point
    is that the older commit's CI genuinely passed; deploying it is merely no
    longer correct, which is not the same claim as broken.
    """
    guard = _job("guard")
    check_step = next(s for s in guard["steps"] if s.get("id") == "check")
    run = str(check_step["run"])
    # The mismatch branch must set should_deploy=false and continue (no exit
    # 1 on that path) — only an actual resolution failure (empty tip_sha) may
    # exit non-zero.
    assert 'should_deploy=false" >> "$GITHUB_OUTPUT"' in run
    lines = run.splitlines()
    stale_branch_start = next(
        i for i, line in enumerate(lines) if "should_deploy=false" in line
    )
    # No `exit 1` between the stale branch and the next top-level `else`/`fi`.
    stale_branch = []
    for line in lines[stale_branch_start:]:
        stale_branch.append(line)
        if line.strip() in ("else", "fi"):
            break
    assert not any("exit 1" in line for line in stale_branch), (
        "the stale-commit branch must not exit non-zero — that would surface "
        "as a job FAILURE, indistinguishable from a genuine error, instead of "
        "a deliberate, harmless skip"
    )


def test_stale_skip_notice_is_visually_distinct_from_a_red_ci_skip() -> None:
    """MUDD-118 and MUDD-150 each cost hours because `skipped` meant two
    different things in the same UI. A stale-tip skip must say so loudly and
    explicitly, not rely on the reader inferring it from a diff of SHAs.
    """
    guard = _job("guard")
    check_step = next(s for s in guard["steps"] if s.get("id") == "check")
    run = str(check_step["run"])
    assert "STALE DEPLOY SKIPPED" in run
    assert "not failed" in run.lower() or "not a" in run.lower(), (
        "the notice/summary should explicitly disclaim that this is a "
        "red-CI-style failure, not just report the two SHAs"
    )
    assert "::notice" in run, "the skip must be loud in the run log, not silent"
    assert "GITHUB_STEP_SUMMARY" in run, (
        "the skip must also appear in the job summary — the log alone is "
        "easy to miss on a run nobody expected to matter"
    )


def test_workflow_dispatch_bypasses_the_tip_guard() -> None:
    """The break-glass path (§3, docs/CI_CD_POLICY.md) exists specifically to
    deploy a commit that is deliberately NOT the tip (e.g. a rollback) — the
    guard must never block it.
    """
    guard = _job("guard")
    dispatch_step = next(s for s in guard["steps"] if s.get("id") == "dispatch")
    assert str(dispatch_step.get("if", "")) == "${{ github.event_name == 'workflow_dispatch' }}"
    assert "should_deploy=true" in str(dispatch_step["run"])
