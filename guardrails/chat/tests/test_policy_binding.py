"""Policy binding: flipping a guardrails/policy control changes the verdict (AC5)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from guardrails.chat.egress import ChatEgressGuard, OutboundTurn
from guardrails.chat.policy import ControlBinding, ControlBindingError, default_controls_path
from guardrails.chat.turn import ChatTurnGuard
from guardrails.chat.verdict import DecisionLevel


def _control(control_id, *, enabled, mode="block", rationale=""):
    """One schema-valid control entry (all required fields present)."""
    entry = {
        "id": control_id,
        "name": control_id,
        "description": f"whether {control_id} is enforced",
        "enabled": enabled,
        "mode": mode,
        "implemented_by": [f"guardrails/policy/bundles/platform/{control_id}.yaml"],
        "since": "issue #26",
    }
    if rationale:
        entry["on_since_rationale"] = rationale
    return entry


def test_the_shipped_registry_carries_every_bound_control_default_off(binding_off):
    """The lane's binding is real: each bound control exists and ships OFF."""
    assert binding_off.undecidable is False
    assert binding_off.bound_missing() == ()
    assert set(binding_off.bound_ids()) <= set(binding_off.registered_ids())
    assert binding_off.active_ids() == ()
    assert all(binding_off.is_active(control_id) is False for control_id in binding_off.bound_ids())


def test_an_egress_redaction_is_warned_while_the_control_is_off(pii_turn):
    outcome = ChatEgressGuard(controls=ControlBinding.default()).guard(pii_turn)
    assert outcome.decision is DecisionLevel.WARN
    assert outcome.aborted is False
    assert "<REDACTED_EMAIL>" in outcome.dispatch_text


def test_flipping_data_egress_guard_on_refuses_the_same_input(pii_turn, make_binding):
    """One fixed input, one flipped control, a different verdict - proven both ways."""
    binding = make_binding("data-egress-guard")
    assert binding.undecidable is False
    assert binding.active_ids() == ("data-egress-guard",)

    outcome = ChatEgressGuard(controls=binding).guard(pii_turn)

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.aborted is True
    assert outcome.dispatch_text == ""
    assert "data-egress-guard" in outcome.outcome.reason
    assert any("egress:data-egress-guard" in item for item in outcome.findings)

    # ...and the same payload with the control OFF still proceeds, redacted.
    off = ChatEgressGuard(controls=ControlBinding.default()).guard(pii_turn)
    assert off.decision is not outcome.decision
    assert off.dispatch_text != outcome.dispatch_text


def test_flipping_tool_use_guard_on_refuses_an_injection_in_tool_arguments(injection_turn):
    """The tool-argument injection analysis is genuinely gated by the control."""
    off = ChatEgressGuard(controls=ControlBinding.default()).guard(injection_turn)
    assert off.decision is DecisionLevel.LOG
    assert off.aborted is False

    on = ChatEgressGuard(controls=ControlBinding.with_controls_enabled(["tool-use-guard"])).guard(
        injection_turn
    )
    assert on.decision is DecisionLevel.BLOCK
    assert on.aborted is True
    assert on.dispatch_text == ""
    assert "tool-use-guard" in on.outcome.reason
    assert any("inj.override.ignore_previous" in item for item in on.findings)


def test_the_turn_guard_records_the_controls_it_resolved(guard, clean_turn, make_binding):
    plain = guard.guard_turn(clean_turn, controls=ControlBinding.default())
    assert plain.policy.ran is True
    assert plain.policy.decision is DecisionLevel.LOG
    assert plain.policy.evidence["active"] == []
    assert set(plain.policy.evidence["bound"]) <= set(plain.policy.evidence["registered"])

    enabled = guard.guard_turn(clean_turn, controls=make_binding("data-egress-guard"))
    assert enabled.policy.evidence["active"] == ["data-egress-guard"]


def test_a_bound_control_missing_from_the_registry_is_undecidable():
    """A control-gated behaviour with no toggle registered is a formality."""
    document = yaml.safe_load(Path(default_controls_path()).read_text(encoding="utf-8"))
    document["controls"] = [
        control for control in document["controls"] if control["id"] != "data-egress-guard"
    ]
    binding = ControlBinding.from_mapping(document)

    assert binding.undecidable is False
    assert binding.bound_missing() == ("data-egress-guard",)

    outcome = ChatTurnGuard().guard_turn(
        OutboundTurn(user_prompt="Summarize the incident ticket."), controls=binding
    )
    assert outcome.aborted is True
    assert outcome.undecidable is True
    assert outcome.policy.ran is False
    assert "not registered" in outcome.policy.reason


def test_an_unreadable_registry_is_undecidable(tmp_path):
    binding = ControlBinding.from_path(tmp_path / "absent-controls.yaml")
    assert binding.undecidable is True
    assert "cannot read controls registry" in binding.error

    outcome = ChatTurnGuard().guard_turn(
        OutboundTurn(user_prompt="Summarize the incident ticket."), controls=binding
    )
    assert outcome.aborted is True
    assert outcome.undecidable is True
    assert outcome.policy.ran is False


def test_an_invalid_registry_is_undecidable():
    """Two controls with one id: the registry is ambiguous, so the turn refuses."""
    binding = ControlBinding.from_mapping(
        {
            "version": 1,
            "controls": [
                _control("data-egress-guard", enabled=False),
                _control("data-egress-guard", enabled=False),
            ],
        }
    )
    assert binding.undecidable is True
    assert "invalid controls registry" in binding.error
    assert "duplicate control id" in binding.error


def test_enabling_a_control_the_registry_does_not_carry_is_undecidable():
    binding = ControlBinding.with_controls_enabled(["not-a-real-control"])
    assert binding.undecidable is True
    assert "not present in the controls registry" in binding.error


def test_enabling_nothing_is_undecidable():
    assert ControlBinding.with_controls_enabled([]).undecidable is True


def test_an_enabled_control_still_needs_its_rationale(make_binding):
    """The flip goes through the registry's own validation (AO-GR-6)."""
    binding = make_binding("tool-use-guard")
    assert binding.undecidable is False
    assert binding.is_active("tool-use-guard") is True

    stripped = {
        "version": 1,
        "controls": [_control("tool-use-guard", enabled=True)],
    }
    assert ControlBinding.from_mapping(stripped).undecidable is True


def test_a_control_this_lane_does_not_bind_cannot_be_consulted(binding_off):
    with pytest.raises(ControlBindingError) as raised:
        binding_off.is_active("model-call-budget")
    assert "not bound by the chat guard" in str(raised.value)
