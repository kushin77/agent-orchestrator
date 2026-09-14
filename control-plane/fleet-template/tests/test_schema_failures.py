"""A schema violation is a HARD failure, and CANNOT-ASSESS is never OK.

The no-false-green requirement: an input that does not satisfy the declared
contract must fail loudly (exit 2 in the gate, an exception in-process) and
must never be reported as a pass or as an empty finding list.
"""

from __future__ import annotations

import copy
import os

import pytest

import render


def _write_yaml(path: str, document: object) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render.dump_yaml(document))


def _pilot_params(lane_root, pilot):
    return render.load_yaml(os.path.join(lane_root, render.PILOTS_DIR, f"{pilot}.params.yaml"))


def test_a_missing_required_section_is_a_hard_failure(renderer, template):
    broken = copy.deepcopy(template)
    del broken["spec"]["finops"]
    with pytest.raises(render.SchemaViolation) as excinfo:
        renderer(template_doc=broken)
    assert "finops" in str(excinfo.value)


def test_a_bad_run_state_value_is_a_hard_failure(renderer, template):
    broken = copy.deepcopy(template)
    broken["spec"]["roles"][0]["state"] = "running"
    with pytest.raises(render.SchemaViolation) as excinfo:
        renderer(template_doc=broken)
    assert "state" in str(excinfo.value)


def test_an_unknown_domain_is_a_hard_failure(renderer, lane_root):
    params = _pilot_params(lane_root, "agent-orchestrator")
    params["values"]["domains"][0]["name"] = "not-a-real-domain"
    with pytest.raises(render.SchemaViolation) as excinfo:
        renderer(params_doc=params, observations=None)
    assert "domains" in str(excinfo.value)


def test_a_negative_budget_is_a_hard_failure(renderer, lane_root):
    params = _pilot_params(lane_root, "agent-orchestrator")
    params["values"]["budget"]["fleet_total"] = -5
    with pytest.raises(render.SchemaViolation):
        renderer(params_doc=params, observations=None)


def test_a_missing_required_parameter_is_a_hard_failure(renderer, lane_root):
    params = _pilot_params(lane_root, "agent-orchestrator")
    del params["values"]["model_tiers"]
    with pytest.raises(render.SchemaViolation):
        renderer(params_doc=params, observations=None)


def test_an_extra_key_in_a_rendered_instance_is_rejected(bundle, committed_instance):
    instance = committed_instance("agent-orchestrator")
    instance["definition"]["unexpected"] = True
    violations = render.validate(instance, bundle["instance"], bundle)
    assert any("unexpected" in violation for violation in violations)
    with pytest.raises(render.SchemaViolation):
        render.require_valid(instance, "instance", bundle)


def test_a_missing_committed_instance_field_reaches_exit_code_2(lane_root, lane_copy):
    """`check` reports CANNOT-ASSESS (rc 2) for a schema-invalid instance, never 0."""
    root = lane_copy()
    path = os.path.join(root, render.PILOTS_DIR, "agent-orchestrator.fleet.yaml")
    broken = render.load_yaml(path)
    del broken["definition"]["finops"]
    _write_yaml(path, broken)

    result = render.check(root)
    assert result.status == render.STATUS_CANNOT_ASSESS
    assert result.exit_code == render.EXIT_CANNOT_ASSESS
    assert result.cannot_assess
    assert not result.findings


def test_validate_instance_cli_is_exit_2_for_a_corrupt_instance(lane_copy):
    root = lane_copy()
    path = os.path.join(root, render.PILOTS_DIR, "shared-frontend.fleet.yaml")
    broken = render.load_yaml(path)
    broken["run_state"]["present"] = "not-a-list"
    _write_yaml(path, broken)

    rc = render.main(["--root", root, "validate-instance", path])
    assert rc == render.EXIT_CANNOT_ASSESS
    assert rc != render.EXIT_OK


def test_a_missing_schema_bundle_is_cannot_assess(lane_copy):
    root = lane_copy()
    os.remove(os.path.join(root, render.SCHEMA_FILE))
    result = render.check(root)
    assert result.status == render.STATUS_CANNOT_ASSESS
    assert result.exit_code == render.EXIT_CANNOT_ASSESS


def test_the_schema_validator_accepts_the_committed_documents(bundle, lane_root, template, committed_instance):
    assert render.validate(template, bundle["template"], bundle) == []
    for pilot in ("agent-orchestrator", "shared-frontend"):
        assert render.validate(committed_instance(pilot), bundle["instance"], bundle) == []
        params = _pilot_params(lane_root, pilot)
        assert render.validate(params, bundle["params"], bundle) == []
        observations = render.load_yaml(
            os.path.join(lane_root, render.PILOTS_DIR, params["observations"])
        )
        assert render.validate(observations, bundle["run_state_document"], bundle) == []
