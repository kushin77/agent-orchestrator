"""The CLI's exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PKG = Path("paperclip/adapters/skills")


def run_cli(repo_root: Path, data_root: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    """Drive the adapter CLI with the real code against a data root."""
    return subprocess.run(
        [sys.executable, "-m", "paperclip.adapters.skills.cli", "--root", str(data_root), *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )


def test_check_is_ok(repo_root: Path) -> None:
    result = run_cli(repo_root, repo_root, "check")
    assert result.returncode == 0, result.stderr


def test_project_check_is_ok(repo_root: Path) -> None:
    result = run_cli(repo_root, repo_root, "project", "--check")
    assert result.returncode == 0, result.stderr


def test_project_emits_the_projection(repo_root: Path) -> None:
    result = run_cli(repo_root, repo_root, "project")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["source_function"] == "gateway.mcp.tools.build_registry"
    assert data["tools"] == sorted(data["tools"])


def test_load_allowed_is_zero(repo_root: Path) -> None:
    result = run_cli(repo_root, repo_root, "load", "--skill", "ticket-contract-read",
                     "--profile", "paperclip")
    assert result.returncode == 0, result.stderr


def test_load_refused_is_one(repo_root: Path) -> None:
    result = run_cli(repo_root, repo_root, "load", "--skill", "mcp-tool-projection",
                     "--profile", "coder")
    assert result.returncode == 1
    assert "research" in result.stderr


def test_check_fails_on_an_undeclared_skill(scratch_root: Path, repo_root: Path) -> None:
    rogue = scratch_root / PKG / "library" / "rogue-skill"
    rogue.mkdir(parents=True)
    (rogue / "SKILL.md").write_text(
        "---\nid: rogue-skill\nkind: skill\nname: Rogue\ndescription: rogue\n"
        "provenance:\n  repo: example/rogue\n  path: x/SKILL.md\n"
        "  license: MIT\n  verdict: REFERENCE\n---\n# Rogue\n",
        encoding="utf-8",
    )
    result = run_cli(repo_root, scratch_root, "check")
    assert result.returncode == 1
    assert "rogue-skill" in result.stderr


def test_unknown_profile_is_cannot_assess(repo_root: Path) -> None:
    result = run_cli(repo_root, repo_root, "load", "--skill", "ticket-contract-read",
                     "--profile", "no-such-profile")
    assert result.returncode == 2


def test_status_reports_counts(repo_root: Path) -> None:
    result = run_cli(repo_root, repo_root, "status")
    assert result.returncode == 0
    assert "registry:" in result.stdout
    assert "projection:" in result.stdout
