"""Fail-closed / no-false-green tests (issue #26 acceptance #5).

A gate that cannot fire or that silently passes on an unparseable/unknown
policy protects nothing.  These tests prove the engine fails CLOSED — to BLOCK
with the reason in the evidence — whenever it cannot honestly evaluate, and
that a malformed policy fails at deploy time rather than at runtime.
"""

from __future__ import annotations

import pytest

from policy import (
    ConditionError,
    DecisionLevel,
    Policy,
    PolicyBundle,
    PolicyEngine,
    PolicyRule,
    PolicyValidationError,
)
from policy.controls import ControlRegistry
from policy.loader import policy_from_mapping


def _unknown_op_rule_policy() -> Policy:
    """A Policy built directly (bypassing loader validation) carrying a rule
    whose condition uses an operator the loader would reject — proving the
    ENGINE also fails closed as a second line of defense."""
    return Policy(
        id="crafted",
        version=1,
        name="crafted",
        description="runtime fail-closed probe",
        default=DecisionLevel.LOG,
        enabled=True,
        controls=(),
        rules=(
            PolicyRule(
                id="uses-bogus-op",
                actions=("demo.action",),
                decision=DecisionLevel.BLOCK,
                reason="bogus op block",
                condition={"path": "demo.flagged", "op": "not_a_real_op", "value": True},
            ),
        ),
    )


def test_unknown_operator_fails_closed_at_runtime():
    engine = PolicyEngine(PolicyBundle((_unknown_op_rule_policy(),)), controls=ControlRegistry())
    result = engine.evaluate("demo.action", context={"demo": {"flagged": True}})
    assert result.decision is DecisionLevel.BLOCK
    assert result.error is not None
    assert "unknown condition operator" in result.error
    # the failure is audit-logged as a block with the error
    record = engine.audit_log.records()[-1]
    assert record.outcome == "blocked"
    assert record.error is not None


def test_missing_required_context_path_fails_closed():
    """A comparison on an absent context attribute cannot be proven -> BLOCK."""
    doc = {
        "id": "budget-like",
        "rules": [
            {
                "id": "block-over",
                "actions": ["model.call"],
                "decision": "block",
                "reason": "over budget",
                "condition": {"path": "budget.utilization_ratio", "op": "gte", "value": 1.0},
            }
        ],
    }
    engine = PolicyEngine(
        PolicyBundle((policy_from_mapping(doc, source="test"),)), controls=ControlRegistry()
    )
    result = engine.evaluate("model.call", tenant="acme", context={})
    assert result.decision is DecisionLevel.BLOCK
    assert result.error is not None
    assert "absent from the context" in result.error


def test_no_matching_policy_blocks_by_default_negative_control():
    """The canonical negative control: an action with no governing policy is
    BLOCKED (deny-by-default), not silently allowed."""
    engine = PolicyEngine(PolicyBundle(), controls=ControlRegistry())
    result = engine.evaluate("ungoverned.action", subject="anyone", tenant="acme")
    assert result.decision is DecisionLevel.BLOCK
    assert result.uncovered is True
    assert result.error is None
    assert result.matched_rules == ()


def test_malformed_policy_fails_at_deploy_not_runtime():
    """An invalid policy never reaches the engine — the startup gate rejects it."""
    bad_docs = [
        {},  # missing everything
        {"id": "x"},  # missing rules
        {"id": "x", "rules": [{"id": "r", "actions": ["a"], "decision": "maybe"}]},  # bad enum
    ]
    for doc in bad_docs:
        with pytest.raises(PolicyValidationError):
            policy_from_mapping(doc, source="test")


def test_condition_error_is_typed_for_fail_closed_handling():
    from policy.conditions import evaluate_condition

    with pytest.raises(ConditionError):
        evaluate_condition({"path": "a.b", "op": "gte", "value": 1}, {})


def test_egress_missing_classification_fails_closed(shipped_bundle_dir, all_controls_on):
    """A real example: egress with no classification cannot be evaluated."""
    from policy.startup import build_bundle

    bundle = build_bundle([shipped_bundle_dir], controls=all_controls_on)
    engine = PolicyEngine(bundle, controls=all_controls_on)
    result = engine.evaluate(
        "egress.send",
        tenant="acme",
        context={"egress": {"destination": "https://x.example"}},  # no classification
    )
    assert result.decision is DecisionLevel.BLOCK
    assert result.error is not None
