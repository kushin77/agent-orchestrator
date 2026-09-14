"""Drift: a committed instance that diverges from its template params is NOT-OK.

Drift is the failure mode that makes a "template" a lie -- the instance stops
being a render of the params and becomes a hand-maintained file nobody
notices. Each test here mutates exactly one input in a scratch copy of the
lane and requires the gate to notice.
"""

from __future__ import annotations

import os

import render


def _write_yaml(path: str, document: object) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render.dump_yaml(document))


def _lane_ok(root: str) -> render.Assessment:
    result = render.check(root)
    assert result.status == render.STATUS_OK, result.lines()
    return result


def test_a_clean_lane_is_ok(lane_root):
    result = _lane_ok(lane_root)
    assert result.detail["pilots"] == ["fleet-agent-orchestrator", "fleet-shared-frontend"]


def test_a_tampered_committed_instance_is_not_ok(lane_copy):
    root = lane_copy()
    _lane_ok(root)

    path = os.path.join(root, render.PILOTS_DIR, "agent-orchestrator.fleet.yaml")
    instance = render.load_yaml(path)
    instance["definition"]["roles"][2]["parallelism"] = 3
    _write_yaml(path, instance)

    result = render.check(root)
    assert result.status == render.STATUS_NOT_OK
    assert result.exit_code == render.EXIT_NOT_OK
    assert any(finding.code == "RENDER_DRIFT" for finding in result.findings)


def test_editing_params_without_re_rendering_is_not_ok(lane_copy):
    root = lane_copy()
    path = os.path.join(root, render.PILOTS_DIR, "shared-frontend.params.yaml")
    params = render.load_yaml(path)
    params["values"]["domains"][0]["labels"].append("extra-label")
    _write_yaml(path, params)

    result = render.check(root)
    assert result.status == render.STATUS_NOT_OK
    codes = {finding.code for finding in result.findings}
    assert "PARAMS_SHA_DRIFT" in codes
    assert "PARAMS_DIGEST_DRIFT" in codes


def test_editing_the_template_without_re_rendering_is_not_ok(lane_copy):
    root = lane_copy()
    path = os.path.join(root, render.TEMPLATE_FILE)
    template = render.load_yaml(path)
    template["spec"]["roles"][0]["description"] = "tampered description"
    _write_yaml(path, template)

    result = render.check(root)
    assert result.status == render.STATUS_NOT_OK
    assert any(finding.code == "TEMPLATE_SHA_DRIFT" for finding in result.findings)


def test_a_missing_committed_instance_is_not_ok(lane_copy):
    root = lane_copy()
    os.remove(os.path.join(root, render.PILOTS_DIR, "shared-frontend.fleet.yaml"))

    result = render.check(root)
    assert result.status == render.STATUS_NOT_OK
    assert any(finding.code == "PILOT_INSTANCE_MISSING" for finding in result.findings)


def test_a_pilot_whose_name_disagrees_with_its_repo_is_not_ok(lane_copy):
    """The pilot file name is the instance path; a mismatch is a missing instance."""
    root = lane_copy()
    source = os.path.join(root, render.PILOTS_DIR, "agent-orchestrator.params.yaml")
    target = os.path.join(root, render.PILOTS_DIR, "mislabelled.params.yaml")
    with open(source, "r", encoding="utf-8") as handle:
        _write_yaml(target, render.yaml.safe_load(handle))

    result = render.check(root)
    assert result.status == render.STATUS_NOT_OK
    codes = {finding.code for finding in result.findings}
    assert "PILOT_NAME_MISMATCH" in codes
    assert "PILOT_INSTANCE_MISSING" in codes


def test_a_lane_with_no_pilots_is_cannot_assess(lane_copy):
    root = lane_copy()
    for name in os.listdir(os.path.join(root, render.PILOTS_DIR)):
        if name.endswith(".params.yaml"):
            os.remove(os.path.join(root, render.PILOTS_DIR, name))

    result = render.check(root)
    assert result.status == render.STATUS_CANNOT_ASSESS
    assert result.exit_code == render.EXIT_CANNOT_ASSESS


def test_drift_is_silent_for_identical_documents(renderer):
    committed = renderer("agent-orchestrator")
    assert render.check_drift(committed, renderer("agent-orchestrator")) == []


def test_drift_reports_a_nested_change_once_with_its_path(renderer):
    committed = renderer("agent-orchestrator")
    fresh = renderer("agent-orchestrator")
    fresh["definition"]["smes"][0]["labels"][0] = "changed-label"

    findings = render.check_drift(committed, fresh)
    assert len(findings) == 1
    assert findings[0].code == "RENDER_DRIFT"
    assert "smes[0].labels[0]" in findings[0].message
