"""Registry adaptation: reuse of the policy loader and the portal policy map."""

from __future__ import annotations

import pytest

from controls.model import ControlError, ControlSet
from controls.registry import (
    build_control_set,
    cross_check_policy_map,
    default_controls_path,
    load_control_policy_map,
    load_controls,
    load_state,
    save_state,
)


def test_controls_load_and_default_off(controls):
    assert controls, "the shipped registry must declare at least one control"
    assert all(control.default_enabled is False for control in controls)


def test_every_control_is_in_the_reused_policy_map(controls):
    policy_map = load_control_policy_map()
    assert policy_map, "the reused CONTROL_POLICY_MAP must not be empty"
    assert cross_check_policy_map(controls, policy_map) == ()


def test_policy_map_carries_console_semantics():
    policy_map = load_control_policy_map()
    entry = policy_map["model-call-budget"]
    assert entry["source"] == "guardrails"
    assert entry["gated_actions"] == ["model.call"]


def test_default_path_is_the_guardrails_registry():
    path = default_controls_path()
    assert path.name == "controls.yaml"
    assert path.parent.name == "policy"
    assert path.parent.parent.name == "guardrails"


def test_state_round_trips_on_disk(tmp_path):
    path = tmp_path / "nested" / "state.json"
    assert load_state(path) == {}
    save_state(path, {"model-call-budget": True})
    assert load_state(path) == {"model-call-budget": True}


def test_build_control_set_hydrates_from_state(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, {"model-call-budget": True})
    control_set = build_control_set(state_path=path)
    assert isinstance(control_set, ControlSet)
    assert control_set.is_enabled("model-call-budget") is True
    assert "tool-use-guard" not in control_set.enabled_ids()


def test_unknown_control_id_in_state_is_ignored(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, {"ghost-control": True})
    control_set = build_control_set(state_path=path)
    assert "ghost-control" not in control_set
    assert control_set.enabled_ids() == ()


def test_a_control_that_ships_on_in_the_registry_is_refused(tmp_path):
    registry = tmp_path / "controls.yaml"
    registry.write_text(
        "version: 1\n"
        "controls:\n"
        "  - id: ships-on\n"
        "    name: Ships ON\n"
        "    description: must be refused\n"
        "    enabled: true\n"
        "    mode: block\n"
        "    implemented_by:\n"
        "      - guardrails/policy/bundles/platform/model-call-budget.yaml\n"
        '    since: "test"\n'
        '    on_since_rationale: "test-only"\n',
        encoding="utf-8",
    )
    with pytest.raises(ControlError):
        load_controls(registry)
