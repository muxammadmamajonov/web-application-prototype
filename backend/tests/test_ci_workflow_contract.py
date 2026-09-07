"""CI must never let one wedged step hold a self-hosted runner indefinitely.

All five self-hosted runners live on ONE physical Mac mini. That makes two
failure modes possible that a hosted-runner setup never sees, and both actually
happened:

1. **A step that hangs forever.** On 2026-08-26 a `Start ephemeral Postgres`
   step sat in `docker run -d` for **9 hours**. Docker Desktop had wedged
   mid-start: the container stayed in `Created`, and the CLI never returned.
   The step's own retry loop was written as `if docker run ...; then` — it can
   only react to a NON-ZERO EXIT, so a command that never returns bypasses it
   entirely. Nothing else bounded the job, so the runner was held until a human
   killed the process. Every other queued job — including the production
   synthetic monitor — starved behind it.

2. **Runs fighting over one fixed host resource.** `migrations` and
   `integration` each start their own throwaway Postgres. Giving them one
   fixed host port each (55432 / 55442) stopped the two jobs of a single run
   from colliding, but not two RUNS: a PR and `main` on this same machine
   reach the same literal bind, and the loser's `docker rm -f <fixed name>`
   deletes the winner's live database mid-job. That is what cancelled PR
   #998's `HTTP integration` after a full 45-minute timeout (run
   32991148066, attempt 1). Both the port and the container name are now
   scoped to the run instead.

These tests pin the invariants, not the literals: bound the work, and let no
two runs share a fixed host resource. The timeout VALUES may be tuned freely;
their existence may not. Likewise the port must stay dynamic — pinning a new
literal would only move the collision, not remove it.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

# Jobs are the top-level keys under `jobs:` — two-space indent, then a key.
_JOB_RE = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.MULTILINE)


def _workflow() -> str:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _job_blocks() -> dict[str, str]:
    """Split ci.yml into {job_name: job_body}. Body runs to the next job key."""
    text = _workflow()
    jobs_at = text.index("\njobs:\n")
    body = text[jobs_at:]
    marks = [(m.group(1), m.start()) for m in _JOB_RE.finditer(body)]
    assert marks, "no jobs parsed out of ci.yml — the splitter is wrong, not the workflow"
    blocks: dict[str, str] = {}
    for i, (name, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(body)
        blocks[name] = body[start:end]
    return blocks


def test_every_job_is_time_bounded() -> None:
    """No job may be able to hold a runner forever.

    This is the backstop for failure mode 1. Even if a future step forgets its
    own timeout, the job-level cap still frees the runner.
    """
    missing = [
        name for name, block in _job_blocks().items()
        if not re.search(r"^    timeout-minutes:\s*\d+", block, re.MULTILINE)
    ]
    assert not missing, (
        f"jobs without a job-level timeout-minutes: {missing}. "
        "One wedged step in an unbounded job starves all five runners — that is "
        "how 2026-08-26 lost 9 hours of CI."
    )


def test_postgres_provisioning_is_safe_for_the_selected_runner() -> None:
    """`docker run` can hang without returning; the retry loop cannot catch that.

    The loop reacts to a non-zero exit code. A hung CLI produces no exit code at
    all, so the bound has to sit on the step itself.
    """
    blocks = _job_blocks()
    for name in ("migrations", "integration"):
        block = blocks[name]
        if "runs-on: ubuntu-latest" in block:
            assert "services:" in block and "image: postgres:16" in block
            assert '--health-cmd "pg_isready -U postgres"' in block
        else:
            assert "- name: Start ephemeral Postgres" in block

    # Each such step must carry its own timeout-minutes before the next `- name:`.
    for match in re.finditer(r"- name: Start ephemeral Postgres\n(.*?)(?=\n      - name: )", _workflow(), re.S):
        assert re.search(r"^        timeout-minutes:\s*\d+", match.group(1), re.MULTILINE), (
            "a Start ephemeral Postgres step has no timeout-minutes; a Docker "
            "container-start wedge would hang it indefinitely"
        )


def test_ephemeral_postgres_binds_no_fixed_host_port() -> None:
    """A literal host port is a cross-RUN collision, not just a cross-job one.

    The original fix here gave `migrations` and `integration` one fixed port
    each (55432 / 55442) so the two jobs of a single run could not fight. That
    held only as long as one run existed at a time. Two runs on this Mac mini
    -- a PR and `main`, or two PRs -- reach the SAME literal bind, and the
    second one's `docker run -p 55442:5432` cannot bind. Measured, not
    theorised: PR #998's `HTTP integration` burned its full 45-minute timeout
    and was `cancelled` (run 32991148066, attempt 1) for exactly this.

    `-p 0:5432` asks the kernel for a free port and the step reads it back with
    `docker port`. Two runs then cannot collide by construction, so no host
    mutex has to serialise them and no job spends its timeout budget queueing
    behind another run.
    """
    blocks = _job_blocks()
    for name in ("migrations", "integration"):
        assert name in blocks, f"job `{name}` disappeared from ci.yml"
        if "runs-on: ubuntu-latest" in blocks[name]:
            assert "- 5432:5432" in blocks[name]
            continue
        fixed = set(re.findall(r"-p (\d{4,5}):5432", blocks[name]))
        assert not fixed, (
            f"job `{name}` publishes fixed host port(s) {sorted(fixed)}. Two "
            "concurrent RUNS on this one machine reach the same bind; use "
            "`-p 0:5432` and read the assigned port back with `docker port`."
        )
        assert "-p 0:5432" in blocks[name], (
            f"job `{name}` no longer asks for a kernel-assigned host port"
        )
        # Match the command, not the words: `docker port` also appears in the
        # explanatory comment above the step, and a comment must never be able
        # to satisfy a behavioural assertion. (Caught by mutation: deleting the
        # real invocation left the comment behind and this test still passed.)
        assert re.search(r'docker port "\$container" 5432/tcp', blocks[name]), (
            f"job `{name}` binds port 0 but never reads the assigned port back "
            "with `docker port` — nothing could connect to that database"
        )


def test_ephemeral_postgres_container_name_is_scoped_to_its_run() -> None:
    """`docker rm -f <fixed name>` is how one run killed another run's database.

    Both start steps begin by removing a stale container of the same name. With
    a fixed name that `rm -f` is indiscriminate: run B's start step deletes run
    A's live Postgres mid-job, and A then fails somewhere far away from the
    cause. Scoping the name to `github.run_id` makes that removal only ever
    able to reach an earlier, dead attempt of the SAME run.
    """
    blocks = _job_blocks()
    for name in ("migrations", "integration"):
        block = blocks[name]
        if "runs-on: ubuntu-latest" in block:
            assert "services:" in block and "image: postgres:16" in block
            continue
        decl = re.search(r'container="([^"]+)"', block)
        assert decl, f"job `{name}` no longer names its Postgres container in one place"
        assert "github.run_id" in decl.group(1), (
            f"job `{name}`'s Postgres container name `{decl.group(1)}` is not "
            "scoped to the run — a concurrent run's `docker rm -f` would delete it"
        )
        assert not re.search(r"docker rm -f ci-pg-\w+(\s|$)", block), (
            f"job `{name}` still removes a container by a fixed, unscoped name"
        )


def test_postgres_urls_are_derived_from_the_assigned_port_not_hardcoded() -> None:
    """A half-finished port change is worse than no change.

    With a dynamic port the failure mode inverts: the danger is no longer a
    stale literal in one place, it is a Postgres URL that still carries ANY
    literal port. Such a URL points at whatever else happens to be listening --
    another run's database, most likely -- and the job's isolation assertions
    silently measure the wrong server. Every URL must instead be written to
    $GITHUB_ENV by the start step, from the port `docker port` reported.
    """
    blocks = _job_blocks()
    for name in ("migrations", "integration"):
        block = blocks[name]
        if "runs-on: ubuntu-latest" in block:
            declared = set(re.findall(r"^      (\w*DATABASE_URL\w*): postgresql\+\w+://", block, re.MULTILINE))
            assert declared, f"job `{name}` declares no Postgres URLs"
            assert all("localhost:5432" in line for line in block.splitlines() if "DATABASE_URL" in line and "postgresql+" in line)
            continue
        hardcoded = set(
            re.findall(r"postgresql\+\w+://[^\s]*?localhost:(\d{4,5})/", block)
        )
        assert not hardcoded, (
            f"job `{name}` still hardcodes Postgres host port(s) {sorted(hardcoded)}; "
            "the port is assigned at runtime, so every URL must be built from "
            "`docker port` output and exported via $GITHUB_ENV"
        )
        exported = set(re.findall(r'(\w*DATABASE_URL\w*)=postgresql\+\w+://', block))
        assert exported, (
            f"job `{name}` neither hardcodes nor exports any Postgres URL — "
            "nothing would be able to connect"
        )
        # Every one of those URLs must land in $GITHUB_ENV, so check the URLs
        # that are actually inside a `{ ... } >> "$GITHUB_ENV"` group rather
        # than just that the block mentions $GITHUB_ENV somewhere. (Caught by
        # mutation: redirecting the URL group to /dev/null still left
        # `PG_CONTAINER=... >> "$GITHUB_ENV"` behind, and the loose form of
        # this assertion passed while no URL reached any later step.)
        persisted: set[str] = set()
        for group in re.findall(
            r"\{\n(.*?)\n\s*\} >> \"\$GITHUB_ENV\"", block, re.DOTALL
        ):
            persisted |= set(
                re.findall(r'(\w*DATABASE_URL\w*)=postgresql\+\w+://', group)
            )
        assert exported == persisted, (
            f"job `{name}` builds {sorted(exported - persisted)} but never writes "
            "them to $GITHUB_ENV, so no later step can see them"
        )


def test_each_postgres_job_cleans_up_unconditionally() -> None:
    """A container surviving a failed job poisons the next run's port bind."""
    for name in ("migrations", "integration"):
        block = _job_blocks()[name]
        if "runs-on: ubuntu-latest" in block:
            assert "services:" in block, f"`{name}` has no disposable service container"
            continue
        assert "- name: Stop ephemeral Postgres" in block, f"`{name}` never stops its Postgres"
        stop = block[block.index("- name: Stop ephemeral Postgres"):]
        assert re.search(r"^        if: always\(\)", stop, re.MULTILINE), (
            f"`{name}`'s Stop ephemeral Postgres is not `if: always()` — it would "
            "be skipped exactly when the job failed and cleanup matters most"
        )


def test_the_scan_image_is_scanned_exactly_once() -> None:
    """A second full scan of the same image is pure cost.

    `image-scan` used to run Trivy twice over `muddato-api:scan` — identical
    image, identical severity, identical vuln-type, differing only in output
    format. The second pass fed a SARIF upload into GitHub code scanning, which
    this repository does not have (`GET /code-scanning/analyses` answers "Code
    scanning is not enabled"), and the upload carried `continue-on-error: true`
    so it reported success regardless. ~200s of every run, for a report nobody
    could open.

    If Advanced Security is ever enabled, scan ONCE to JSON and use
    `trivy convert` for the extra format — do not re-add a second scan.
    """
    block = _job_blocks()["image-scan"]
    scans = re.findall(r"image-ref: (\S+)", block)
    assert scans, "image-scan runs no Trivy scan at all — the gate is gone"
    assert len(scans) == len(set(scans)), (
        f"image {scans} is scanned more than once in the same job. Scan once and "
        "convert, rather than paying for a second full scan."
    )


def test_the_scan_image_tag_is_scoped_to_its_run() -> None:
    """A fixed image tag is the same cross-run bug as a fixed host port.

    `image-scan` builds, scans, then `docker rmi -f`s its image. With a fixed
    `muddato-api:scan` tag on a machine that runs several workflow runs at once,
    two things follow: run B's build retags the image while run A is scanning it
    -- so A reports a verdict about a commit that is not A's -- and whichever
    run finishes first deletes the tag in its `if: always()` cleanup while the
    other is still using it. Both are silent; neither turns the job red for the
    right reason.

    Scoping the tag to `github.run_id` removes the sharing, which is what allows
    the host-resource mutex to be deleted (ops/runner/hooks/job-started.sh) --
    and that mutex was itself charging its wait time to each job's
    `timeout-minutes` budget.
    """
    block = _job_blocks()["image-scan"]
    tag = re.search(r"^      SCAN_IMAGE: (\S.*)$", block, re.MULTILINE)
    assert tag, "image-scan no longer declares its scan image in one place"
    assert "github.run_id" in tag.group(1), (
        f"scan image tag `{tag.group(1)}` is not scoped to the run — a "
        "concurrent run would retag or delete it mid-scan"
    )
    # And nothing may quietly go back to referring to the bare tag. Strip
    # comments first: the step above *explains* the old `muddato-api:scan` tag
    # in prose, and prose must neither satisfy nor trip a behavioural check.
    code = "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )
    stray = re.findall(r"muddato-api:scan(?![-$])", code)
    assert not stray, (
        "image-scan still references the unscoped tag `muddato-api:scan` "
        f"({len(stray)}x) — build, scan and cleanup must all use $SCAN_IMAGE"
    )


def test_the_container_gate_still_blocks_on_fixable_high_and_critical() -> None:
    """Removing the unread SARIF report must not soften the gate that blocks.

    The removed step never blocked anything — it was `continue-on-error: true`.
    This asserts the one that DOES block is intact, so a future cleanup cannot
    quietly turn the image scan into decoration.

    Parsed as YAML rather than matched as text: the surrounding comments now
    *describe* `continue-on-error`, and a raw substring search cannot tell an
    explanation apart from a setting.
    """
    import yaml

    workflow = yaml.safe_load(_workflow())
    steps = workflow["jobs"]["image-scan"]["steps"]
    gate = next(
        (s for s in steps if s.get("name") == "Trivy scan (fail on fixable HIGH/CRITICAL)"),
        None,
    )
    assert gate is not None, "the blocking Trivy step was renamed or removed"

    params = gate.get("with", {})
    assert str(params.get("exit-code")) == "1", "the Trivy gate no longer fails the build"
    assert params.get("severity") == "HIGH,CRITICAL", "the gate no longer covers HIGH/CRITICAL"
    assert params.get("ignore-unfixed") is True, "the gate's unfixed policy changed"
    assert gate.get("continue-on-error") is not True, (
        "the Trivy gate gained continue-on-error — it would report success while "
        "letting fixable HIGH/CRITICAL vulnerabilities through"
    )
