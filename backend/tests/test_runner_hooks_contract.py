"""Runner host hooks must never delete a Docker resource by a resolved ID.

`ops/runner/hooks/job-completed.sh` runs on the bare Mac mini host after every
CI job — the only place that can catch a job torn down by `timeout-minutes` or
`cancel-in-progress` without running its `if: always()` cleanup steps. It must
touch ONLY what the current job/run/attempt created, or it becomes the exact
cross-run collision MUDD-90 exists to remove.

The scan-image cleanup used to resolve its own tag to an image ID with
`docker images -q <tag>` and then `docker rmi -f <ID>`. Two concurrent runs
building identical `backend/` content produce the SAME image ID via Docker's
layer cache, under two DIFFERENT run-scoped tags — `docker rmi -f <ID>` deletes
every tag pointing at that ID, so one run's cleanup untagged the other's
still-scanning image. Measured, not theorised: runs 33080783145 and
33080790075 both failed `image-scan` with `unable to find the specified image
"muddato-api:scan-<run>-1"`. `docker rmi -f <tag>` cannot reach a sibling run's
tag even when the layers are shared, so the fix is to delete the tag directly
and never resolve through an ID lookup first.
"""
from __future__ import annotations

import re
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "ops" / "runner" / "hooks" / "job-completed.sh"


def _hook() -> str:
    assert HOOK.is_file(), f"missing {HOOK}"
    return HOOK.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """Strip comment lines so prose explaining the old bug can't satisfy or
    trip an assertion about the actual code (the explanation above names the
    exact command this test forbids)."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def test_scan_image_is_never_resolved_to_an_id_before_deletion() -> None:
    code = _code(_hook())
    assert "docker images -q" not in code, (
        "job-completed.sh resolves a tag to an image ID before deleting it. "
        "`docker rmi -f <ID>` deletes EVERY tag pointing at that ID, so a "
        "concurrent run sharing the same Docker layer cache loses its own tag "
        "too. Delete the tag directly instead of looking up its ID."
    )


def test_scan_image_is_removed_by_its_run_scoped_tag() -> None:
    text = _code(_hook())
    assert re.search(
        r'docker rmi -f "\$\{?TAG\}?"', text
    ) or re.search(
        r'docker rmi -f "muddato-api:scan-\$\{RUN\}-\$\{ATTEMPT\}"', text
    ), (
        "job-completed.sh no longer deletes the scan image by its literal, "
        "run-scoped tag (muddato-api:scan-${RUN}-${ATTEMPT})"
    )


def test_scan_image_cleanup_is_gated_to_the_image_scan_job() -> None:
    text = _hook()
    assert re.search(r'\[\s*"\$JOB"\s*=\s*"image-scan"\s*\]', text), (
        "job-completed.sh must only remove the scan image when GITHUB_JOB is "
        "image-scan — any other job of the same run finishing first would "
        "delete the image while image-scan is still scanning it"
    )


def test_container_cleanup_filter_is_scoped_to_job_run_and_attempt() -> None:
    text = _hook()
    assert re.search(
        r'--filter "name=\^ci-pg-\$\{JOB\}-\$\{RUN\}-\$\{ATTEMPT\}\$"', text
    ), (
        "job-completed.sh's container filter must be anchored to job, run AND "
        "attempt — matching on run alone would remove a sibling job's still-"
        "live Postgres container from the same run"
    )
