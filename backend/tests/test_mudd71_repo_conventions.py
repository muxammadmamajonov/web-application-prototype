"""MUDD-71 — the repo's own instructions must not sabotage the repo.

These are documentation and configuration assertions, which is unusual for a
test suite. They are here because the finding they close (MUDD-40 M6) is a
*security* finding, not a style one: `frontend/pnpm-workspace.yaml` carries three
`overrides` that close CVEs Dependabot could not resolve itself, and **`npm` does
not read that file at all**. `CLAUDE.md` told every human and agent to run
`npm run …`. Following the project's own instruction silently regenerated the
dependency tree from the raw ranges and re-opened all three.

Nothing in CI catches that: `npm` prints no warning, the install succeeds, and
the frontend builds. The only thing that can fail is an assertion like this one.

The rest cover the same shape at a lower cost: a source-of-truth document that
transcribes numbers it cannot keep current (M4), an audit artefact hidden inside
a package root (N2), and a deployment fact frozen into source (L1).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO / "CLAUDE.md"
STATUS_MD = REPO / "STATUS.md"
FRONTEND_PKG = REPO / "frontend" / "package.json"
CI_YML = REPO / ".github" / "workflows" / "ci.yml"


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout
    return out.splitlines()


# ── M6: the package manager the docs name must be the one that reads the
#        security overrides ──────────────────────────────────────────────────

def test_pnpm_workspace_overrides_still_exist() -> None:
    """The premise of every assertion below. If the overrides are gone (upstream
    released fixes and Dependabot cleaned up), the npm/pnpm distinction stops
    being a security matter and these tests can be relaxed — deliberately, not
    by accident."""
    ws = (REPO / "frontend" / "pnpm-workspace.yaml").read_text(encoding="utf-8")
    assert "overrides:" in ws, "pnpm-workspace.yaml no longer pins security overrides"


def test_claude_md_never_instructs_npm_run() -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    offenders = [
        f"{i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if "npm run" in line
    ]
    assert not offenders, (
        "CLAUDE.md instructs `npm run`, but npm ignores frontend/pnpm-workspace.yaml "
        "and would drop its security overrides:\n" + "\n".join(offenders)
    )


def test_frontend_pins_pnpm_as_its_package_manager() -> None:
    pkg = json.loads(FRONTEND_PKG.read_text(encoding="utf-8"))
    pm = pkg.get("packageManager")
    assert pm is not None, "frontend/package.json has no `packageManager` field"
    assert pm.startswith("pnpm@"), f"packageManager is not pnpm: {pm!r}"


def test_pinned_pnpm_matches_ci() -> None:
    """A pin that disagrees with CI is worse than no pin: it makes the local tree
    and the gate resolve different dependency graphs while both look green."""
    pkg = json.loads(FRONTEND_PKG.read_text(encoding="utf-8"))
    pinned = pkg["packageManager"].removeprefix("pnpm@").split("+")[0]
    ci = CI_YML.read_text(encoding="utf-8")
    versions = set(re.findall(r"pnpm/action-setup@[^\n]*\n(?:\s*#[^\n]*\n)*\s*with:\n(?:\s*#[^\n]*\n)*\s*version:\s*([0-9][^\s#]*)", ci))
    assert versions, "could not find a pinned pnpm version in ci.yml"
    assert versions == {pinned}, f"ci.yml pins {versions}, package.json pins {pinned!r}"


# ── N2: audit reports live in docs/audits/, never inside a package ──────────

def test_no_audit_artefact_hidden_inside_a_package() -> None:
    offenders = [
        p for p in _tracked_files()
        if re.match(r"^(frontend|backend)/\.audit", p)
    ]
    assert not offenders, (
        "audit reports belong in docs/audits/, not as hidden dotfiles inside a "
        f"package root: {offenders}"
    )


def test_the_mudd17_report_survived_the_move() -> None:
    assert (REPO / "docs" / "audits" / "AUDIT-MUDD-17-frontend.md").exists()


# ── M4: STATUS.md calls itself the single source of truth, so it must not
#        transcribe figures it cannot keep current ──────────────────────────

def _alembic_head() -> str:
    versions = REPO / "backend" / "alembic" / "versions"
    revs, downs = {}, set()
    for f in versions.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        m = re.search(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)", src, re.M)
        d = re.search(r"^down_revision(?::[^=]+)?\s*=\s*[\"']([^\"']+)", src, re.M)
        if m:
            revs[m.group(1)] = f.name
        if d:
            downs.add(d.group(1))
    heads = [r for r in revs if r not in downs]
    assert len(heads) == 1, f"expected a single Alembic head, got {heads}"
    return heads[0]


def test_status_md_does_not_pin_an_alembic_revision() -> None:
    """It pinned `r91smsdedup` while the real head had moved four revisions on —
    and said so under the heading "single source of truth"."""
    text = STATUS_MD.read_text(encoding="utf-8")
    head = _alembic_head()
    stale = [
        rev for rev in re.findall(r"`(r[a-z0-9]{2,}[a-z0-9]*)`", text)
        if re.fullmatch(r"r[fg0-9]\d*[a-z]+", rev) and rev != head
    ]
    section = text.split("## 2.")[0]  # the verification gate, not the finding log
    assert not [r for r in stale if f"`{r}`" in section], (
        f"STATUS.md's verification gate names Alembic revision(s) {stale}; the "
        f"current head is {head!r}. Name the command, not its output."
    )


def test_status_md_verification_gate_carries_no_test_counts() -> None:
    section = STATUS_MD.read_text(encoding="utf-8").split("## 2.")[0]
    offenders = re.findall(r"\d[\d ,]*\s+(?:passed|skipped|tests|files)\b", section)
    assert not offenders, (
        "STATUS.md transcribes run output that goes stale within days: "
        f"{offenders}. Record the command instead."
    )


def test_status_md_was_updated_after_the_fable_pass() -> None:
    text = STATUS_MD.read_text(encoding="utf-8")
    m = re.search(r"_Last updated:\s*(\d{4}-\d{2}-\d{2})", text)
    assert m, "STATUS.md has no `_Last updated:` stamp"
    assert m.group(1) > "2026-06-24", f"STATUS.md still stamped {m.group(1)}"


# ── L1: deployment facts are configuration, not source literals ─────────────

def test_support_channel_id_is_configuration() -> None:
    src = (REPO / "backend" / "app" / "services" / "support_channel.py").read_text(encoding="utf-8")
    code = re.sub(r"#.*$", "", src, flags=re.M)
    assert "settings.support_channel_id" in code
    assert not re.search(r"^CHANNEL_ID\s*=\s*-?\d+", code, re.M), (
        "the support channel id is a bare module constant again"
    )


def test_support_channel_id_defaults_to_the_live_channel() -> None:
    """Configuration with a changed default is a silent outage: leads would post
    into a channel nobody watches."""
    from app.config import Settings

    assert Settings.model_fields["support_channel_id"].default == -1001234567890


def test_canonical_origin_is_build_time_config() -> None:
    src = (REPO / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    code = re.sub(r"//.*$", "", src, flags=re.M)
    assert "VITE_CANONICAL_ORIGIN" in code
    assert "`https://muddato.uz${" not in code, (
        "main.tsx still interpolates the prod domain as a source literal"
    )


# ── the architecture document README already links to ──────────────────────

def test_architecture_doc_exists_and_states_the_layer_rules() -> None:
    """`README.md` has linked `docs/ARCHITECTURE.md` since before this pass; the
    file did not exist. MUDD-40's own conclusion was that three of its findings
    came from that absence — every author made a locally sound call because the
    layer rules were written down nowhere."""
    doc = REPO / "docs" / "ARCHITECTURE.md"
    assert doc.exists(), "README.md links docs/ARCHITECTURE.md but it does not exist"
    text = doc.read_text(encoding="utf-8")
    for token in ("app/api", "app/services", "app/core", "app/models"):
        assert token in text, f"ARCHITECTURE.md does not place `{token}`"


@pytest.mark.parametrize("link", ["docs/ARCHITECTURE.md"])
def test_readme_links_resolve(link: str) -> None:
    assert link in (REPO / "README.md").read_text(encoding="utf-8")
    assert (REPO / link).exists()
