"""CLI + shell-gate exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

The gate is only trustworthy if the mapping from outcome to exit code is
exercised at both layers: the Python module and the shell wrapper that the
orchestrator will wire into `make verify`.
"""

from __future__ import annotations

import os
import subprocess

import pytest

import render


def _write_yaml(path: str, document: object) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render.dump_yaml(document))


def test_render_writes_an_instance_that_reloads_to_the_same_document(tmp_path, lane_root):
    params = os.path.join(lane_root, render.PILOTS_DIR, "agent-orchestrator.params.yaml")
    out = tmp_path / "rendered.yaml"
    assert render.main(["--root", lane_root, "render", "--params", params, "--out", str(out)]) == render.EXIT_OK
    assert render.load_yaml(str(out)) == render.load_yaml(
        os.path.join(lane_root, render.PILOTS_DIR, "agent-orchestrator.fleet.yaml")
    )


def test_render_with_an_unknown_parameter_exits_2(tmp_path, lane_copy):
    root = lane_copy()
    template_path = os.path.join(root, render.TEMPLATE_FILE)
    template = render.load_yaml(template_path)
    template["spec"]["finops"]["currency"] = "${nope.currency}"
    _write_yaml(template_path, template)

    params = os.path.join(root, render.PILOTS_DIR, "agent-orchestrator.params.yaml")
    rc = render.main(["--root", root, "render", "--params", params, "--out", str(tmp_path / "out.yaml")])
    assert rc == render.EXIT_CANNOT_ASSESS


def test_check_exits_0_on_a_clean_lane(lane_root):
    assert render.main(["--root", lane_root, "check"]) == render.EXIT_OK


def test_check_exits_1_on_drift(lane_copy):
    root = lane_copy()
    path = os.path.join(root, render.PILOTS_DIR, "agent-orchestrator.fleet.yaml")
    instance = render.load_yaml(path)
    instance["metadata"]["display_name"] = "hand-edited"
    _write_yaml(path, instance)
    assert render.main(["--root", root, "check"]) == render.EXIT_NOT_OK


def test_check_exits_2_when_nothing_can_be_assessed(lane_copy):
    root = lane_copy()
    os.remove(os.path.join(root, render.TEMPLATE_FILE))
    assert render.main(["--root", root, "check"]) == render.EXIT_CANNOT_ASSESS


def test_check_json_output_carries_the_tri_state(lane_root, capsys):
    assert render.main(["--root", lane_root, "check", "--json"]) == render.EXIT_OK
    payload = capsys.readouterr().out
    assert '"status": "OK"' in payload
    assert '"exit_code": 0' in payload
    assert "fleet-agent-orchestrator" in payload


def test_the_shell_gate_is_green_on_the_lane(repo_root):
    script = os.path.join(repo_root, "scripts", "check-fleet-template.sh")
    assert os.path.isfile(script), f"{script} is missing"
    completed = subprocess.run(
        ["bash", script], cwd=repo_root, capture_output=True, text=True, check=False
    )
    assert completed.returncode == render.EXIT_OK, completed.stdout + completed.stderr
    assert "check-fleet-template: OK" in completed.stdout


@pytest.mark.parametrize(
    "rc,expected_status",
    [
        (render.EXIT_OK, "OK"),
        (render.EXIT_NOT_OK, "NOT-OK"),
        (render.EXIT_CANNOT_ASSESS, "CANNOT-ASSESS"),
    ],
)
def test_the_status_vocabulary_is_stable(rc, expected_status):
    assessment = render.Assessment()
    if rc == render.EXIT_NOT_OK:
        assessment.findings.append(render.Finding("X", "y"))
    elif rc == render.EXIT_CANNOT_ASSESS:
        assessment.cannot_assess.append("z")
    assert assessment.status == expected_status
    assert assessment.exit_code == rc


def test_a_cannot_assess_lane_never_reports_ok(lane_copy):
    root = lane_copy()
    os.remove(os.path.join(root, render.SCHEMA_FILE))
    assert render.main(["--root", root, "check"]) != render.EXIT_OK


def test_not_ok_dominates_cannot_assess_in_aggregation(lane_copy):
    """Aggregation is fail-closed: a real defect outranks an unassessable input."""
    root = lane_copy()
    os.remove(os.path.join(root, render.PILOTS_DIR, "shared-frontend.fleet.yaml"))
    with open(os.path.join(root, render.PILOTS_DIR, "agent-orchestrator.fleet.yaml")) as handle:
        broken = render.yaml.safe_load(handle)
    del broken["definition"]["finops"]
    _write_yaml(os.path.join(root, render.PILOTS_DIR, "agent-orchestrator.fleet.yaml"), broken)

    result = render.check(root)
    assert result.findings and result.cannot_assess
    assert result.status == render.STATUS_NOT_OK
    assert result.exit_code == render.EXIT_NOT_OK
