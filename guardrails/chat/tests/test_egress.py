"""Outbound DLP egress: block aborts, redaction proceeds, nothing echoes (issue #507 AC1)."""

from __future__ import annotations

import json

import pytest

from guardrails.chat.egress import ChatEgressGuard, OutboundTurn
from guardrails.chat.policy import ControlBinding
from guardrails.chat.verdict import DecisionLevel


class ExplodingScrubber:
    """A scrub engine whose catalog cannot be read (the undecidable path)."""

    def scrub(self, text: str):  # pragma: no cover - always raises
        from dlp.catalog import CatalogError

        raise CatalogError("no rule catalog on disk")


@pytest.fixture()
def egress():
    return ChatEgressGuard()


def test_a_seeded_secret_is_blocked_and_the_call_is_aborted(egress, secret_turn, secret):
    outcome = egress.guard(secret_turn)

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.aborted is True
    assert outcome.dispatch_text == ""
    assert outcome.allowed is False
    assert "secret.generic_api_key" in outcome.outcome.reason
    assert outcome.components[0].verdict == "blocked"
    assert outcome.components[0].sha256 == ""
    assert secret in secret_turn.user_prompt


def test_neither_the_verdict_nor_the_finding_echoes_the_matched_secret(egress, secret_turn, secret):
    outcome = egress.guard(secret_turn)
    record = json.dumps(outcome.to_dict(), sort_keys=True)

    assert secret not in record
    assert secret not in outcome.outcome.reason
    assert all(secret not in item for item in outcome.findings)
    assert all(secret not in component.name for component in outcome.components)
    # the record is still attributable: it names the rule that fired
    assert "secret.generic_api_key" in record
    assert any("class=secret" in item for item in outcome.findings)


def test_only_the_blocking_component_is_named_not_its_content(egress, secret, clean_turn):
    turn = OutboundTurn(
        user_prompt=clean_turn.user_prompt,
        grounding_prefix=f"operator note: {secret}",
        tool_arguments={"runbook": "restore the replica"},
    )
    outcome = egress.guard(turn)

    assert outcome.aborted is True
    assert outcome.dispatch_text == ""
    assert any(
        component.name == "grounding_prefix" and component.blocked
        for component in outcome.components
    )
    assert secret not in json.dumps(outcome.to_dict())


def test_a_redaction_path_lets_the_payload_proceed_without_the_value(egress, pii_turn):
    outcome = egress.guard(pii_turn)

    assert outcome.decision is DecisionLevel.WARN
    assert outcome.aborted is False
    assert outcome.allowed is True
    assert "ada.lovelace@contoso.example" not in outcome.dispatch_text
    assert "<REDACTED_EMAIL>" in outcome.dispatch_text
    assert any("pii.email" in item for item in outcome.findings)
    assert outcome.components[0].redacted is True


def test_a_clean_turn_passes_with_a_log_verdict(egress, clean_turn):
    outcome = egress.guard(clean_turn)

    assert outcome.decision is DecisionLevel.LOG
    assert outcome.aborted is False
    assert outcome.findings == ()
    assert outcome.components[0].verdict == "sent"
    assert "handover note" in outcome.dispatch_text


def test_tool_arguments_are_scrubbed_too(egress, secret):
    turn = OutboundTurn(user_prompt="Call the tool.", tool_arguments={"auth_blob": secret})
    outcome = egress.guard(turn)

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.aborted is True
    assert secret not in json.dumps(outcome.to_dict())


def test_tool_arguments_are_normalized_from_the_shapes_callers_use():
    mapping = OutboundTurn(user_prompt="p", tool_arguments={"a": "one", "b": "two"})
    assert mapping.tool_arguments == (("a", "one"), ("b", "two"))

    pairs = OutboundTurn(user_prompt="p", tool_arguments=[("a", "one")])
    assert pairs.tool_arguments == (("a", "one"),)

    bare = OutboundTurn(user_prompt="p", tool_arguments=["one", "two"])
    assert bare.tool_arguments == (("arg0", "one"), ("arg1", "two"))

    with pytest.raises(TypeError):
        OutboundTurn(user_prompt="p", tool_arguments="not arguments")

    with pytest.raises(TypeError):
        OutboundTurn(user_prompt="p", tool_arguments=[1])


def test_a_turn_with_nothing_to_inspect_is_refused_not_logged(egress, chat):
    outcome = egress.guard(OutboundTurn(user_prompt=""))

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.outcome.ran is False
    assert outcome.aborted is True
    assert "nothing was inspected" in outcome.outcome.reason


def test_a_scrub_engine_that_cannot_run_is_undecidable_never_log(chat):
    guard = ChatEgressGuard(scrubber=ExplodingScrubber())
    outcome = guard.guard(OutboundTurn(user_prompt="Summarize the ticket."))

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.outcome.ran is False
    assert outcome.aborted is True
    assert outcome.dispatch_text == ""
    assert "dlp scrub failed" in outcome.outcome.reason


def test_an_unresolvable_control_binding_is_undecidable_never_log(clean_turn):
    guard = ChatEgressGuard(controls=ControlBinding(None, error="controls.yaml is missing"))
    outcome = guard.guard(clean_turn)

    assert outcome.decision is DecisionLevel.BLOCK
    assert outcome.outcome.ran is False
    assert "control binding unresolved" in outcome.outcome.reason


def test_a_control_the_lane_does_not_bind_cannot_be_consulted(chat, binding_off):
    with pytest.raises(chat.ControlBindingError):
        binding_off.is_active("model-call-budget")
