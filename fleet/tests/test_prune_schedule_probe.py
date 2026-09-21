"""prune-worktrees.sh --schedule reads config/fleet-jobs.json (issue #1627).

Under the single-developer method (docs/EXECUTION-PLAN.md §7, 2026-09-21) the
host crontab is retired, so `crontab -l` is permanently empty regardless of
whether the reaper is meant to run. The schedule is IaC now: declared in
`config/fleet-jobs.json` and rendered by `fleet/cron.py`. `--schedule` must
report SCHEDULED when the manifest declares an enabled, scheduled job whose
command invokes this tool, and CANNOT-ASSESS only when the manifest itself is
absent/unreadable — never NOT-SCHEDULED just because the host crontab is bare.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prune-worktrees.sh"


def _run_schedule_probe(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), "--schedule"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_schedule_ok_when_manifest_declares_it(tmp_path):
    # A throwaway git checkout with only the manifest the probe reads — no
    # host crontab involved at all, proving the probe does not depend on one.
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "fleet-jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "name": "reap",
                        "command": "bash scripts/prune-worktrees.sh --apply",
                        "schedule": "47 3 * * *",
                        "enabled": True,
                    }
                ]
            }
        )
    )
    result = _run_schedule_probe(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "SCHEDULED" in result.stdout
    assert "NOT-SCHEDULED" not in result.stdout


def test_cannot_assess_when_manifest_absent(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    result = _run_schedule_probe(tmp_path)
    assert result.returncode == 2
    assert "CANNOT-ASSESS" in result.stdout


def test_not_scheduled_when_job_disabled(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "fleet-jobs.json").write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "name": "reap",
                        "command": "bash scripts/prune-worktrees.sh --apply",
                        "schedule": "47 3 * * *",
                        "enabled": False,
                    }
                ]
            }
        )
    )
    result = _run_schedule_probe(tmp_path)
    assert result.returncode == 1
    assert "NOT-SCHEDULED" in result.stdout
