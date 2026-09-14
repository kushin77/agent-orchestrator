"""Negative controls / mutation proof for the fleet-template gate.

A check that cannot fail is a formality. Each test below makes the specific
defect its check exists to catch, requires the check to report it, then
disables that one check and requires the same defect to become invisible --
which is what proves the original detection came from the check under test and
not from something incidental.
"""

from __future__ import annotations

import os
import subprocess

import render


def _write_yaml(path: str, document: object) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render.dump_yaml(document))


def _re_render(root: str, stem: str) -> None:
    """Re-render a pilot's committed instance inside ``root``.

    Uses the same observation lookup as `render.check`, so the regenerated
    artifact is drift-free by construction and the test measures the signal it
    says it measures.
    """
    bundle, _ = render.load_bundle(root)
    template_path = os.path.join(root, render.TEMPLATE_FILE)
    params_path = os.path.join(root, render.PILOTS_DIR, f"{stem}.params.yaml")
    params_doc = render.load_yaml(params_path)
    observations = None
    if params_doc.get("observations"):
        observations = render.load_yaml(
            os.path.join(os.path.dirname(params_path), params_doc["observations"])
        )
    instance = render.render_instance(
        render.load_yaml(template_path),
        params_doc,
        bundle,
        render.read_bytes(template_path),
        render.read_bytes(params_path),
        os.path.relpath(params_path, root),
        observations,
    )
    _write_yaml(os.path.join(root, render.PILOTS_DIR, f"{stem}.fleet.yaml"), instance)


def test_the_drift_check_detects_a_hand_edit_and_is_its_only_cause(lane_copy, monkeypatch):
    root = lane_copy()
    path = os.path.join(root, render.PILOTS_DIR, "agent-orchestrator.fleet.yaml")
    instance = render.load_yaml(path)
    instance["definition"]["roles"][2]["effort"] = "high"
    _write_yaml(path, instance)

    result = render.check(root)
    assert result.status == render.STATUS_NOT_OK
    assert [finding.code for finding in result.findings] == ["RENDER_DRIFT"]

    monkeypatch.setattr(render, "check_drift", lambda committed, fresh: [])
    assert render.check(root).status == render.STATUS_OK


def test_the_drift_check_is_not_vacuous_on_identical_inputs(renderer):
    committed = renderer("agent-orchestrator")
    assert render.check_drift(committed, committed) == []
    assert render.check_drift(committed, renderer("shared-frontend")) != []


def test_the_isolation_check_detects_a_borrowed_namespace(lane_copy, monkeypatch):
    """Borrowing another repo's namespace is caught by check_isolation alone.

    Both pilots then render the same fleet identity, so the lane also trips the
    pilot/name contract -- which is why this asserts the ISOLATION_* signal
    disappears when the check is disabled rather than that the lane turns OK.
    """
    root = lane_copy()
    params_path = os.path.join(root, render.PILOTS_DIR, "shared-frontend.params.yaml")
    params = render.load_yaml(params_path)
    params["repo"] = "agent-orchestrator"
    params["values"]["repo"] = "agent-orchestrator"
    params.pop("observations", None)
    _write_yaml(params_path, params)
    _re_render(root, "shared-frontend")

    result = render.check(root)
    assert result.status == render.STATUS_NOT_OK, result.lines()
    isolation_codes = [f.code for f in result.findings if f.code.startswith("ISOLATION_")]
    assert "ISOLATION_SHARED_FLEET_ID" in isolation_codes
    assert "ISOLATION_SHARED_MEMBER" in isolation_codes

    monkeypatch.setattr(render, "check_isolation", lambda instances: [])
    after = render.check(root)
    assert not [f for f in after.findings if f.code.startswith("ISOLATION_")]
    assert [f.code for f in after.findings] == ["PILOT_NAME_MISMATCH"]


def test_the_isolation_check_is_not_vacuous_on_the_real_pilots(renderer, pilots):
    instances = [renderer(name) for name in pilots]
    assert render.check_isolation(instances) == []
    assert render.check_isolation(instances + [instances[0]]) != []


def test_the_invariant_check_detects_a_broken_ceiling(lane_copy, monkeypatch):
    """A schema-valid fleet that breaks its own FinOps ceiling is the only finding.

    The lane is re-rendered after the params edit, so drift is zero and the
    ceiling invariant is the one signal left -- which is what makes disabling
    it sufficient to turn the lane green.
    """
    root = lane_copy()
    params_path = os.path.join(root, render.PILOTS_DIR, "shared-frontend.params.yaml")
    params = render.load_yaml(params_path)
    params["values"]["budget"]["per_role"]["soldier"] = 100000
    _write_yaml(params_path, params)
    _re_render(root, "shared-frontend")

    result = render.check(root)
    assert result.status == render.STATUS_NOT_OK, result.lines()
    assert [finding.code for finding in result.findings] == ["FINOPS_CEILING_EXCEEDED"]

    monkeypatch.setattr(render, "check_invariants", lambda inst: [])
    assert render.check(root).status == render.STATUS_OK


def test_the_run_state_invariant_detects_a_false_present(lane_copy, monkeypatch):
    root = lane_copy()
    path = os.path.join(root, render.PILOTS_DIR, "agent-orchestrator.fleet.yaml")
    instance = render.load_yaml(path)
    unverified = instance["run_state"]["tri_state"]["unverified"]
    assert unverified, "fixture has nothing unverified to corrupt"
    instance["run_state"]["present"] = list(unverified)
    instance["run_state"]["counts"]["present"] = len(unverified)
    _write_yaml(path, instance)

    codes = [finding.code for finding in render.check(root).findings]
    assert "UNVERIFIED_REPORTED_PRESENT" in codes

    monkeypatch.setattr(render, "check_invariants", lambda inst: [])
    after = [finding.code for finding in render.check(root).findings]
    assert "UNVERIFIED_REPORTED_PRESENT" not in after
    assert set(after) == {"RENDER_DRIFT"}


def test_the_schema_gate_detects_a_mutated_document(bundle, committed_instance):
    instance = committed_instance("agent-orchestrator")
    assert render.validate(instance, bundle["instance"], bundle) == []

    instance["isolation"]["exclusive"] = False
    assert render.validate(instance, bundle["instance"], bundle) != []


def test_the_shell_gate_maps_all_three_outcomes(repo_root, lane_copy):
    script = os.path.join(repo_root, "scripts", "check-fleet-template.sh")

    def run(lane):
        return subprocess.run(
            ["bash", script, "--root", lane], cwd=repo_root, capture_output=True, text=True, check=False
        )

    clean = run(lane_copy("clean"))
    assert clean.returncode == render.EXIT_OK, clean.stdout + clean.stderr
    assert "check-fleet-template: OK" in clean.stdout

    drifted_root = lane_copy("drifted")
    instance_path = os.path.join(drifted_root, render.PILOTS_DIR, "shared-frontend.fleet.yaml")
    drifted = render.load_yaml(instance_path)
    drifted["metadata"]["display_name"] = "hand-edited"
    _write_yaml(instance_path, drifted)
    drifted_run = run(drifted_root)
    assert drifted_run.returncode == render.EXIT_NOT_OK, drifted_run.stdout + drifted_run.stderr
    assert "check-fleet-template: NOT-OK" in drifted_run.stderr

    unassessable_root = lane_copy("unassessable")
    os.remove(os.path.join(unassessable_root, render.TEMPLATE_FILE))
    unassessable = run(unassessable_root)
    assert unassessable.returncode == render.EXIT_CANNOT_ASSESS
    assert "check-fleet-template: CANNOT-ASSESS" in unassessable.stderr
