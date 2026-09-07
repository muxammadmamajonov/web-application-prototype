"""SEC-005 — every backend dependency install must resolve from the lockfile.

`uv sync` alone installs from `uv.lock` but does not *assert* that the lock still
matches `pyproject.toml`; a drifted manifest would be re-resolved silently, which
is the window a malicious or simply unexpected version could enter through.
`--locked` turns that drift into a build failure.

The frontend is already frozen (`pnpm install --frozen-lockfile`) and is asserted
here too so the two halves cannot diverge. Static guard: it reads the workflows.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _run_lines() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for line in wf.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("run:") or stripped.startswith("- run:"):
                out.append((wf.name, stripped))
    return out


def test_workflows_are_discoverable() -> None:
    """A guard that silently matched no files would pass forever."""
    assert WORKFLOWS.is_dir()
    assert len(list(WORKFLOWS.glob("*.yml"))) >= 4
    assert _run_lines()


def test_every_uv_sync_asserts_the_lockfile() -> None:
    """`uv sync` must carry --locked (or --frozen) wherever CI installs deps."""
    offenders = [
        f"{wf}: {line}"
        for wf, line in _run_lines()
        if re.search(r"\buv sync\b", line) and not re.search(r"--locked|--frozen", line)
    ]
    assert offenders == [], f"unlocked backend install(s): {offenders}"


def test_frontend_install_stays_frozen() -> None:
    """The already-implemented frontend control must not regress."""
    installs = [
        (wf, line) for wf, line in _run_lines() if re.search(r"\bpnpm install\b", line)
    ]
    assert installs, "expected at least one pnpm install in CI/deploy"
    for wf, line in installs:
        assert "--frozen-lockfile" in line, f"{wf}: unfrozen pnpm install: {line}"


def test_detector_would_catch_an_unlocked_command() -> None:
    """Self-test: the regex must actually flag a bare `uv sync`."""
    bad = "run: uv sync --dev"
    good = "run: uv sync --locked --dev"
    assert re.search(r"\buv sync\b", bad) and not re.search(r"--locked|--frozen", bad)
    assert re.search(r"--locked|--frozen", good)


# ── Docker: deterministic dependency installs (Pass 2) ──────────────────────
DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def _dockerfile_run_lines() -> list[str]:
    """RUN lines from the backend image, continuations joined."""
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("RUN ")]


def test_dockerfile_is_discoverable() -> None:
    assert DOCKERFILE.is_file()
    assert any("uv sync" in ln for ln in _dockerfile_run_lines()), "expected a uv install step"


def test_every_docker_uv_sync_is_frozen() -> None:
    """Each dependency install in the image must resolve from the lockfile."""
    offenders = [
        ln for ln in _dockerfile_run_lines()
        if re.search(r"\buv sync\b", ln) and not re.search(r"--frozen|--locked", ln)
    ]
    assert offenders == [], f"unfrozen docker install(s): {offenders}"


def test_dockerfile_has_no_unlocked_fallback() -> None:
    """`uv sync --frozen … || uv sync …` silently defeats the frozen install:
    any lock drift falls through to an unpinned resolve. Fail closed instead."""
    offenders = []
    for ln in _dockerfile_run_lines():
        if "uv sync" not in ln:
            continue
        # every uv-sync invocation on the line must carry the flag
        for part in re.split(r"\|\||&&|;", ln):
            if re.search(r"\buv sync\b", part) and not re.search(r"--frozen|--locked", part):
                offenders.append(part.strip())
    assert offenders == [], f"unlocked fallback branch(es): {offenders}"


def test_docker_uv_supports_relative_exclude_newer() -> None:
    """The image resolver must understand our 7-day supply-chain cooldown.

    uv 0.5 treated ``exclude-newer = \"7 days\"`` as an invalid calendar date,
    logged a warning, and ignored the policy.  Pin the first supported modern
    series (0.11+) and keep the image digest immutable.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(
        r"ghcr\.io/astral-sh/uv:(\d+)\.(\d+)\.(\d+)@sha256:([0-9a-f]{64})",
        text,
    )
    assert match, "uv image must be version- and digest-pinned"
    version = tuple(int(part) for part in match.group(1, 2, 3))
    assert version >= (0, 11, 0), "Docker uv is too old for relative exclude-newer"


def test_fallback_detector_would_catch_a_regression() -> None:
    """Self-test: the splitter must flag the exact pattern being removed."""
    bad = "RUN uv sync --frozen --no-dev || uv sync --no-dev"
    parts = [p for p in re.split(r"\|\||&&|;", bad)
             if re.search(r"\buv sync\b", p) and not re.search(r"--frozen|--locked", p)]
    assert len(parts) == 1, "detector failed to see the unlocked fallback"
