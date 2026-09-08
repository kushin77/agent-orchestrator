"""CLI tests — startup gate exit codes and evaluate/controls subcommands.

The CLI is exercised as a real subprocess so exit codes are the honest gate
contract: 0 valid/allowed, 1 invalid/gate-error, 2 blocked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_POLICY_ROOT = Path(__file__).resolve().parents[1]   # guardrails/policy
_REPO_ROOT = Path(__file__).resolve().parents[3]     # agent-orchestrator
_CLI = _POLICY_ROOT / "cli.py"


def run_cli(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        capture_output=True,
        text=True,
        cwd=cwd or str(_REPO_ROOT),
        env=env,
        timeout=60,
    )


def _budget_on_registry(tmp_path: Path) -> Path:
    """Registry registering all three shipped controls but enabling only
    model-call-budget (startup requires every referenced control to be
    registered, so the others must be present even while OFF)."""
    path = tmp_path / "controls-budget-on.yaml"
    controls = []
    for cid in ("model-call-budget", "tool-use-guard", "data-egress-guard"):
        enabled = "true" if cid == "model-call-budget" else "false"
        rationale = "\n    on_since_rationale: test registry" if cid == "model-call-budget" else ""
        controls.append(
            "  - id: " + cid + "\n"
            "    name: " + cid + "\n"
            "    description: " + cid + " for cli tests\n"
            "    enabled: " + enabled + "\n"
            "    mode: block\n"
            "    implemented_by: [guardrails/policy/bundles/platform]\n"
            "    since: 'issue #26'" + rationale
        )
    path.write_text("version: 1\ncontrols:\n" + "\n".join(controls) + "\n", encoding="utf-8")
    return path


def test_validate_shipped_examples_exits_zero():
    proc = run_cli("validate")
    assert proc.returncode == 0, proc.stderr
    assert "valid: yes" in proc.stdout
    assert "policies: 3" in proc.stdout


def test_validate_invalid_bundle_exits_one(tmp_path):
    bundle = tmp_path / "bad-bundle"
    bundle.mkdir()
    (bundle / "bad.yaml").write_text(
        "id: broken\n"
        "rules:\n"
        "  - id: r\n"
        "    actions: [a.b]\n"
        "    decision: explode\n",
        encoding="utf-8",
    )
    proc = run_cli("validate", str(bundle))
    assert proc.returncode == 1
    assert "valid: NO" in proc.stderr
    assert "enum" in proc.stderr


def test_evaluate_blocks_uncovered_action_by_default():
    proc = run_cli("evaluate", "model.call", "--tenant", "acme", "--context", "{}")
    assert proc.returncode == 2  # blocked (controls OFF -> uncovered -> fail closed)
    payload = json.loads(proc.stdout)
    assert payload["decision"] == "block"
    assert payload["uncovered"] is True


def test_evaluate_uncovered_log_exits_zero():
    proc = run_cli("evaluate", "model.call", "--uncovered", "log", "--tenant", "acme", "--context", "{}")
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["decision"] == "log"


def test_evaluate_blocked_and_allowed_with_control_on(tmp_path):
    registry = _budget_on_registry(tmp_path)
    over = run_cli(
        "evaluate", "model.call", "--tenant", "acme",
        "--controls", str(registry),
        "--context", '{"budget": {"utilization_ratio": 1.5}}',
    )
    assert over.returncode == 2
    assert json.loads(over.stdout)["decision"] == "block"

    under = run_cli(
        "evaluate", "model.call", "--tenant", "acme",
        "--controls", str(registry),
        "--context", '{"budget": {"utilization_ratio": 0.1}}',
    )
    assert under.returncode == 0
    assert json.loads(under.stdout)["decision"] == "log"


def test_controls_lists_registry():
    proc = run_cli("controls")
    assert proc.returncode == 0, proc.stderr
    assert "model-call-budget" in proc.stdout
    assert "off" in proc.stdout  # default OFF visible in the listing
