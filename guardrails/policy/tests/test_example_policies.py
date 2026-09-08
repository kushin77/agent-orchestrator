"""Example-policy tests against the shipped bundles/platform examples.

These prove the shipped policies load under the shipped controls registry and
that the gate engine returns the full BLOCK/WARN/LOG tri-state across the
three example surfaces (model call budget, tool use, data egress), including
the flag-gated OFF posture and fail-closed paths.
"""

from __future__ import annotations

import pytest

from policy import DecisionLevel, PolicyEngine, build_bundle


@pytest.fixture()
def engine_all_on(shipped_bundle_dir, all_controls_on) -> PolicyEngine:
    bundle = build_bundle([shipped_bundle_dir], controls=all_controls_on)
    return PolicyEngine(bundle, controls=all_controls_on)


def test_shipped_examples_validate_with_shipped_controls(shipped_bundle_dir, shipped_controls):
    report_bundle = build_bundle([shipped_bundle_dir], controls=shipped_controls)
    assert len(report_bundle) == 3
    assert {p.id for p in report_bundle.policies} == {
        "model-call-budget",
        "tool-use-guard",
        "data-egress-guard",
    }


def test_all_controls_off_blocks_every_action(shipped_bundle_dir, shipped_controls):
    """Flag-gated OFF doctrine: with every control OFF no policy is active, so
    every action is uncovered and the gate fails closed to BLOCK."""
    bundle = build_bundle([shipped_bundle_dir], controls=shipped_controls)
    engine = PolicyEngine(bundle, controls=shipped_controls)
    for action in ("model.call", "tool.use", "egress.send"):
        result = engine.evaluate(action, tenant="acme", context={})
        assert result.decision is DecisionLevel.BLOCK
        assert result.uncovered is True


def test_model_call_budget_tri_state(engine_all_on):
    ctx = lambda ratio: {"budget": {"utilization_ratio": ratio}}
    assert engine_all_on.evaluate("model.call", tenant="acme", context=ctx(1.5)).decision is DecisionLevel.BLOCK
    assert engine_all_on.evaluate("model.call", tenant="acme", context=ctx(0.9)).decision is DecisionLevel.WARN
    assert engine_all_on.evaluate("model.call", tenant="acme", context=ctx(0.1)).decision is DecisionLevel.LOG


def test_model_call_budget_evidence_names_rule(engine_all_on):
    result = engine_all_on.evaluate("model.call", tenant="acme", context={"budget": {"utilization_ratio": 1.5}})
    assert result.matched_rules[0].rule_id == "over-budget-block"
    assert "1.5" in result.matched_rules[0].reason


def test_model_call_missing_utilization_fails_closed(engine_all_on):
    result = engine_all_on.evaluate("model.call", tenant="acme", context={})
    assert result.decision is DecisionLevel.BLOCK
    assert result.error is not None


def test_tool_use_shell_requires_tenant_grant(engine_all_on):
    base = {"tool": {"name": "shell"}}
    granted = {"tool": {"name": "shell"}, "tenant": {"grants": {"shell": True}}}
    assert engine_all_on.evaluate(
        "tool.use", subject="agent-1", tenant="acme", context=base
    ).decision is DecisionLevel.BLOCK
    assert engine_all_on.evaluate(
        "tool.use", subject="agent-1", tenant="acme", context=granted
    ).decision is DecisionLevel.LOG


def test_tool_use_exfil_and_probe_and_benign(engine_all_on):
    exfil = engine_all_on.evaluate("tool.use", tenant="acme", context={"tool": {"name": "exfil.copy"}})
    assert exfil.decision is DecisionLevel.BLOCK
    probe = engine_all_on.evaluate("tool.use", tenant="acme", context={"tool": {"name": "net.scan"}})
    assert probe.decision is DecisionLevel.WARN
    benign = engine_all_on.evaluate("tool.use", tenant="acme", context={"tool": {"name": "search"}})
    assert benign.decision is DecisionLevel.LOG


def test_data_egress_tri_state(engine_all_on):
    pii = {"egress": {"destination": "https://x.example", "classification": "pii"}}
    cleartext = {"egress": {"destination": "http://x.example", "classification": "public"}}
    sensitive = {"egress": {"destination": "https://x.example", "classification": "sensitive"}}
    benign = {"egress": {"destination": "https://x.example", "classification": "public"}}
    assert engine_all_on.evaluate("egress.send", tenant="acme", context=pii).decision is DecisionLevel.BLOCK
    assert engine_all_on.evaluate("egress.send", tenant="acme", context=cleartext).decision is DecisionLevel.BLOCK
    assert engine_all_on.evaluate("egress.send", tenant="acme", context=sensitive).decision is DecisionLevel.WARN
    assert engine_all_on.evaluate("egress.send", tenant="acme", context=benign).decision is DecisionLevel.LOG


def test_shipped_examples_audit_blocks(engine_all_on):
    engine_all_on.evaluate("egress.send", tenant="acme",
                           context={"egress": {"destination": "https://x.example", "classification": "pii"}})
    assert engine_all_on.audit_log.records()[-1].outcome == "blocked"
