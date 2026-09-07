"""The SBOM must stay retrievable without ever touching artifact storage or
a feature this plan cannot use.

(MUDD-52 / N-03) This workflow used to upload the SPDX document as an Actions
artifact. Artifact storage is billed as accumulated GB-days, the
account-level quota is exhausted, and deleting old artifacts does not clear
an already-hit cap.

(MUDD-82) The attestation steps that replaced that upload in #991 never
actually worked either: GitHub attestations are not available on a private,
user-owned, Free-plan repository ("Feature not available for user-owned
private repositories", confirmed live). Every run either failed outright or
completed green while persisting the SBOM nowhere durable. The SBOM is now
pushed to a permanent `sbom-latest` GitHub Release asset instead — a
different quota (repository storage) than the one already hit, and one that
works on this plan.

These tests guard that inversion in all directions: nothing may re-introduce
an artifact upload, nothing may re-introduce an attestation step without
revisiting the plan decision, and the release-asset persistence step that
replaced both may not become optional or silently swallow failures. See
docs/CI_CD_POLICY.md §4.
"""

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sbom.yml"


def _directives() -> str:
    """The workflow's actual YAML, with comment prose stripped out.

    Scanning the raw file is hostile to maintain: the comments explaining
    *why* steps were removed themselves contain the strings being asserted
    against. Assertions must read what the workflow DOES, never what it says
    about itself.
    """
    lines = []
    for line in WORKFLOW.read_text().splitlines():
        code = line.split(" #", 1)[0].rstrip()
        if code.lstrip().startswith("#") or not code.strip():
            continue
        lines.append(code)
    return "\n".join(lines)


def _step(text: str, marker: str) -> str:
    """The single step block containing `marker`, up to the next step."""
    at = text.index(marker)
    start = text.rfind("- name:", 0, at)
    if start == -1:
        start = text.rfind("- uses:", 0, at)
    nxt = text.find("\n      - name:", at)
    nxt_uses = text.find("\n      - uses:", at)
    for candidate in (nxt, nxt_uses):
        if candidate != -1 and (nxt == -1 or candidate < nxt):
            nxt = candidate
    return text[start:] if nxt == -1 else text[start:nxt]


def test_no_artifact_upload_remains() -> None:
    """Neither an explicit upload step nor the generator's implicit one."""
    text = _directives()

    assert "actions/upload-artifact@" not in text
    # anchore/sbom-action uploads its own copy unless told not to; the default
    # is true, so the absence of this line silently re-opens the billing hole.
    assert "upload-artifact: false" in text


def test_no_attestation_steps_remain() -> None:
    """Attestations don't persist on this plan — re-adding them without
    revisiting that plan decision must fail this test, not ship silently."""
    text = _directives()

    assert "actions/attest-sbom@" not in text
    assert "actions/attest-build-provenance@" not in text


def test_sbom_is_persisted_via_release_asset() -> None:
    """The `sbom-latest` Release asset is what replaced the artifact/attestation
    as the durable copy."""
    text = _directives()
    step = _step(text, "gh release upload")

    assert "gh release upload sbom-latest muddato-sbom.spdx.json" in step
    # Without --clobber, the second and every later run fails outright
    # (the asset from the first run already exists under that name).
    assert "--clobber" in step


def test_release_upload_does_not_swallow_failures() -> None:
    """The upload command itself must be able to fail the job. (The
    `gh release view || gh release create` line is legitimate create-if-
    missing control flow, not error suppression — this only inspects the
    `gh release upload` line.)"""
    text = _directives()
    step = _step(text, "gh release upload")
    upload_line = next(
        line for line in step.splitlines() if "gh release upload" in line
    )

    assert "continue-on-error" not in step
    assert "|| true" not in upload_line
    assert not upload_line.rstrip().endswith("|| :")


def test_the_sbom_is_generated_before_it_is_persisted() -> None:
    text = _directives()

    assert text.index("output-file: muddato-sbom.spdx.json") < text.index(
        "gh release upload"
    )


def test_permissions_are_least_privilege_for_release_upload() -> None:
    """`gh release upload` needs `contents: write`; nothing here needs the
    attestation-signing scopes anymore."""
    text = _directives()

    assert "contents: write" in text
    assert "id-token: write" not in text
    assert "attestations: write" not in text
