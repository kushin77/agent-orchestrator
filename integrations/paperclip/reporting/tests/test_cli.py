"""The CLI surface: tri-state, fail-closed, and the same refusals as the API."""

from __future__ import annotations

from conftest import require_real_hub

import json
import shutil
import subprocess
import sys
from pathlib import Path

from governance.modules import registry as module_registry

from integrations.paperclip.reporting.model import ARTIFACT

HUB = "vendor/CMR"


def run(cli: Path, *args: str, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(cli), *args, "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


def cli_of(tree: Path) -> Path:
    return tree / "integrations/paperclip/reporting/cli.py"


def test_compose_prints_the_committed_artifact(repo_root: Path):
    require_real_hub()
    proc = run(repo_root / "integrations/paperclip/reporting/cli.py", "compose", repo=repo_root)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == (repo_root / ARTIFACT).read_text(encoding="utf-8")
    assert proc.stderr == ""


def test_check_is_green_and_says_why(repo_root: Path):
    require_real_hub()
    proc = run(repo_root / "integrations/paperclip/reporting/cli.py", "check", repo=repo_root)
    assert proc.returncode == 0, proc.stderr
    assert "claim(s) resolve" in proc.stdout
    assert "byte-identical to a fresh composition" in proc.stdout


def test_claims_reports_the_claim_count(repo_root: Path):
    require_real_hub()
    proc = run(repo_root / "integrations/paperclip/reporting/cli.py", "claims", repo=repo_root)
    assert proc.returncode == 0, proc.stderr
    assert "claim(s) resolve" in proc.stdout


def test_capability_reports_the_declaration(repo_root: Path):
    require_real_hub()
    proc = run(
        repo_root / "integrations/paperclip/reporting/cli.py", "capability", repo=repo_root
    )
    assert proc.returncode == 0, proc.stderr
    assert "module-brief" in proc.stdout
    assert "file_read, file_write" in proc.stdout


def test_a_capability_the_allowlist_does_not_grant_is_refused_by_name(tree: Path):
    card = tree / "registry/personas/cards/paperclip.yaml"
    card.write_text(card.read_text(encoding="utf-8").replace("  - file_read\n", "", 1), encoding="utf-8")
    proc = run(cli_of(tree), "capability", repo=tree)
    assert proc.returncode == 1
    assert "BRIEF-CAPABILITY-TOOL-UNGRANTED: file_read" in proc.stderr


def test_compose_refuses_to_freeze_a_brief_that_has_findings(tree: Path):
    hub_manifest = tree / HUB / "catalog/modules/code-indexing/module.json"
    hub_manifest.write_text(
        hub_manifest.read_text(encoding="utf-8").replace('"latest": "v0.1.0"', '"latest": null'),
        encoding="utf-8",
    )
    out = tree / "out/MODULE-BRIEF.md"
    proc = run(cli_of(tree), "compose", "--out", str(out), repo=tree)
    assert proc.returncode == 1
    assert "REFUSED to freeze" in proc.stderr
    assert "BRIEF-MODULE-NO-PIN: code-indexing" in proc.stderr
    assert not out.exists(), "a brief with findings must not be frozen"


def test_compose_freezes_a_clean_brief_and_check_agrees(tree: Path):
    out = tree / ARTIFACT
    first = run(cli_of(tree), "compose", "--out", str(out), repo=tree)
    assert first.returncode == 0, first.stderr
    assert out.is_file() and out.stat().st_size > 0
    second = run(cli_of(tree), "compose", repo=tree)
    assert second.returncode == 0, second.stderr
    assert out.read_text(encoding="utf-8") == second.stdout


def test_a_hand_edited_artifact_is_refused_as_stale(tree: Path):
    out = tree / ARTIFACT
    assert run(cli_of(tree), "compose", "--out", str(out), repo=tree).returncode == 0
    out.write_text(out.read_text(encoding="utf-8") + "\nedited by hand\n", encoding="utf-8")
    proc = run(cli_of(tree), "check", repo=tree)
    assert proc.returncode == 1
    assert "BRIEF-STALE: {}".format(ARTIFACT) in proc.stderr


def test_a_registry_document_that_reports_pending_as_shipped_is_refused(tree: Path):
    document = module_registry.build(tree, tree / HUB)
    for entry in document["modules"]:
        if entry["state"] == "target-pending":
            entry["shipped"] = True
            break
    doctored = tree / "doctored-registry.json"
    doctored.write_text(json.dumps(document, indent=2), encoding="utf-8")
    proc = run(cli_of(tree), "compose", "--registry", str(doctored), repo=tree)
    assert proc.returncode == 1
    assert "BRIEF-PENDING-RENDERED-SHIPPED" in proc.stderr


def test_a_tree_with_no_hub_cannot_be_assessed(tree: Path):
    shutil.rmtree(tree / HUB)
    proc = run(cli_of(tree), "compose", repo=tree)
    assert proc.returncode == 2
    assert "CANNOT-ASSESS" in proc.stderr
