"""Example-policy tests against the shipped bundles/platform examples.

These prove the shipped policies load under the shipped controls registry and
that the gate engine returns the full BLOCK/WARN/LOG tri-state across the
example surfaces (model call budget, tool use, data egress, plus the five
workbook mechanical rules of issue #636), including the flag-gated OFF posture
and the fail-closed paths.
"""

from __future__ import annotations

import pytest

from policy import ControlRegistry, DecisionLevel, PolicyEngine, build_bundle


@pytest.fixture()
def engine_all_on(shipped_bundle_dir, all_controls_on) -> PolicyEngine:
    bundle = build_bundle([shipped_bundle_dir], controls=all_controls_on)
    return PolicyEngine(bundle, controls=all_controls_on)


def test_shipped_examples_validate_with_shipped_controls(shipped_bundle_dir, shipped_controls):
    report_bundle = build_bundle([shipped_bundle_dir], controls=shipped_controls)
    ids = {p.id for p in report_bundle.policies}
    # the three issue-#26 platform examples, still shipped and unchanged
    assert {
        "model-call-budget",
        "tool-use-guard",
        "data-egress-guard",
    } <= ids
    # plus the five workbook mechanical rules added by issue #636
    assert {
        "workbook-vector-memory-frontload",
        "workbook-drawio-mcp-diagramming",
        "workbook-external-state-caching",
        "workbook-zero-token-arithmetic",
        "workbook-webhook-caching",
    } <= ids
    # plus the two head-of-org guardrails added by issue #951
    assert {
        "hermes-head-guardrails",
        "paperclip-operator-guardrails",
    } <= ids
    assert len(report_bundle) == 10


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


# --------------------------------------------------------------------------- #
# workbook mechanical rules (issue #636)
#
# The five workbook mechanical enforcement rules are shipped as named policy
# ids in bundles/platform/workbook-mechanical-rules.yaml, each gated behind its
# own control. These tests prove, per rule, that the enforcement is MECHANICAL:
# the decision is driven by one boolean a producer publishes, the refusal names
# the rule, and the affirmative branch is a real allow-list hit rather than a
# silent pass.
# --------------------------------------------------------------------------- #

WORKBOOK_ACTIONS = (
    "workbook.prefetch",
    "workbook.drawio",
    "workbook.external_state",
    "workbook.arithmetic",
    "workbook.webhook",
)


def _workbook_registry(*, on: bool) -> "ControlRegistry":
    """A controls registry for the workbook policy tests.

    Every shipped control is **registered** (the startup gate rejects a bundle
    whose policies name an unregistered control), but only the five workbook
    controls are enabled/disabled by ``on``. The three issue-#26 platform
    controls are always left OFF, so a workbook action is governed exclusively
    by its own workbook policy and the assertions below cannot be satisfied by
    an unrelated gate.
    """
    from policy.controls import ControlRegistry

    platform_ids = (
        "model-call-budget",
        "tool-use-guard",
        "data-egress-guard",
        "hermes-head-guardrails",
        "paperclip-operator-guardrails",
    )
    workbook_ids = (
        "workbook-vector-memory-frontload",
        "workbook-drawio-mcp-diagramming",
        "workbook-external-state-caching",
        "workbook-zero-token-arithmetic",
        "workbook-webhook-caching",
    )

    def _entry(control_id: str, enabled: bool) -> dict:
        entry = {
            "id": control_id,
            "name": control_id,
            "description": f"{control_id} for workbook tests",
            "enabled": enabled,
            "mode": "block",
            "implemented_by": [
                "guardrails/policy/bundles/platform/workbook-mechanical-rules.yaml"
            ],
            "since": "issue #636 tests",
        }
        if enabled:
            # The registry requires a rationale for any control that ships ON.
            entry["on_since_rationale"] = "test-only registry enabling the control"
        return entry

    return ControlRegistry.from_mapping(
        {
            "version": 1,
            "controls": [_entry(cid, False) for cid in platform_ids]
            + [_entry(cid, on) for cid in workbook_ids],
        }
    )


@pytest.fixture()
def workbook_engine(shipped_bundle_dir):
    """Engine over the shipped bundle with ONLY the workbook controls ON.

    The pre-existing #26 controls stay OFF, so a workbook action is governed
    exclusively by its own workbook policy — the assertion cannot be satisfied
    by an unrelated gate.
    """
    from policy import PolicyEngine, build_bundle

    controls = _workbook_registry(on=True)
    bundle = build_bundle([shipped_bundle_dir], controls=controls)
    return PolicyEngine(bundle, controls=controls)


@pytest.fixture()
def shipped_workbook_controls(shipped_controls):
    """The shipped workbook controls, every one default OFF."""
    return tuple(
        control
        for control in shipped_controls.all()
        if control.id.startswith("workbook-")
    )


def test_the_five_workbook_policies_are_declared(shipped_bundle_dir, shipped_controls):
    bundle = build_bundle([shipped_bundle_dir], controls=shipped_controls)
    ids = {policy.id for policy in bundle.policies}
    assert {
        "workbook-vector-memory-frontload",
        "workbook-drawio-mcp-diagramming",
        "workbook-external-state-caching",
        "workbook-zero-token-arithmetic",
        "workbook-webhook-caching",
    } <= ids
    assert len(bundle) == 10


def test_every_workbook_policy_is_control_gated_and_refuses_when_off(
    shipped_bundle_dir, shipped_controls
):
    """Mechanical enforcement means the policy is inert until its control is ON."""
    bundle = build_bundle([shipped_bundle_dir], controls=shipped_controls)
    from policy import PolicyEngine

    engine = PolicyEngine(bundle, controls=shipped_controls)
    for policy in bundle.policies:
        if policy.id.startswith("workbook-"):
            assert policy.controls, f"{policy.id} is not gated behind a control"
    for action in WORKBOOK_ACTIONS:
        result = engine.evaluate(action, tenant="acme", context={})
        assert result.decision is DecisionLevel.BLOCK
        assert result.uncovered is True


def test_the_five_workbook_controls_all_ship_off(shipped_workbook_controls):
    assert len(shipped_workbook_controls) == 5
    for control in shipped_workbook_controls:
        assert control.enabled is False
        assert control.mode == "block"
        assert control.implemented_by == (
            "guardrails/policy/bundles/platform/workbook-mechanical-rules.yaml",
        )


WORKBOOK_CASES = (
    # (action, gate path, affirmative context, policy id, refusal rule)
    (
        "workbook.drawio",
        ("drawio", "mcp_tool_list_cacheable"),
        "workbook-drawio-mcp-diagramming",
        "allow-cacheable-mcp-tool-list",
    ),
    (
        "workbook.external_state",
        ("external_state", "cacheable"),
        "workbook-external-state-caching",
        "allow-cacheable-external-state",
    ),
    (
        "workbook.arithmetic",
        ("arithmetic", "cacheable"),
        "workbook-zero-token-arithmetic",
        "allow-cacheable-arithmetic-result",
    ),
    (
        "workbook.webhook",
        ("webhook", "cacheable"),
        "workbook-webhook-caching",
        "allow-cacheable-webhook-payload",
    ),
)


@pytest.mark.parametrize(
    "action,path,policy_id,rule_id", WORKBOOK_CASES, ids=[c[2] for c in WORKBOOK_CASES]
)
def test_workbook_cache_rules_refuse_anything_not_declared_cacheable(
    workbook_engine, action, path, policy_id, rule_id
):
    """Each cache rule is an allow-list: only a declared-cacheable value passes."""
    # Not cacheable -> refuse.
    refused = workbook_engine.evaluate(
        action, tenant="acme", context={path[0]: {path[1]: False}}
    )
    assert refused.decision is DecisionLevel.BLOCK
    assert refused.blocked is True
    assert refused.matched_rules == (), "an allow-list refusal must not claim a hit"

    # The gate attribute absent entirely -> ALSO refuse (no silent pass).
    absent = workbook_engine.evaluate(action, tenant="acme", context={})
    assert absent.decision is DecisionLevel.BLOCK
    assert absent.error is not None, "an absent required path must fail closed"

    # Cacheable -> allowed, and the allow-list rule is the recorded evidence.
    allowed = workbook_engine.evaluate(
        action, tenant="acme", context={path[0]: {path[1]: True}}
    )
    assert allowed.decision is DecisionLevel.LOG
    assert allowed.blocked is False
    assert [hit.policy_id for hit in allowed.matched_rules] == [policy_id]
    assert [hit.rule_id for hit in allowed.matched_rules] == [rule_id]


def test_workbook_vector_memory_frontload_refuses_a_frontloaded_prefetch(workbook_engine):
    refused = workbook_engine.evaluate(
        "workbook.prefetch",
        tenant="acme",
        context={"prefetch": {"frontloaded": True}},
    )
    assert refused.decision is DecisionLevel.BLOCK
    assert refused.blocked is True
    assert refused.matched_rules[0].policy_id == "workbook-vector-memory-frontload"
    assert refused.matched_rules[0].rule_id == "refuse-frontloaded-vector-memory"
    assert "vector-memory frontload refused" in refused.matched_rules[0].reason

    fresh = workbook_engine.evaluate(
        "workbook.prefetch",
        tenant="acme",
        context={"prefetch": {"frontloaded": False}},
    )
    assert fresh.decision is DecisionLevel.LOG
    assert fresh.blocked is False


def test_a_workbook_refusal_is_audited(workbook_engine):
    workbook_engine.evaluate(
        "workbook.prefetch", tenant="acme", context={"prefetch": {"frontloaded": True}}
    )
    record = workbook_engine.audit_log.records()[-1]
    assert record.outcome == "blocked"
    assert "workbook-vector-memory-frontload" in record.policy_ids
    assert record.rule_ids == ("refuse-frontloaded-vector-memory",)


def test_workbook_policies_do_not_govern_unrelated_actions(workbook_engine):
    """A workbook policy must not accidentally widen to another surface."""
    for action in ("model.call", "tool.use", "egress.send"):
        assert action not in WORKBOOK_ACTIONS


def test_every_workbook_rule_has_a_mechanical_condition(shipped_bundle_dir, shipped_controls):
    """A rule with no condition would decide on prose — reject that shape.

    This is the anti-formality check for #636: each workbook rule must carry a
    ``{path, op, value}`` leaf, i.e. a decision a producer actually publishes.
    """
    bundle = build_bundle([shipped_bundle_dir], controls=shipped_controls)
    checked = 0
    for policy in bundle.policies:
        if not policy.id.startswith("workbook-"):
            continue
        assert policy.rules, f"{policy.id} declares no rule"
        for rule in policy.rules:
            assert rule.condition, f"{policy.id}/{rule.id} has no condition"
            assert set(rule.condition) == {"path", "op", "value"}
            assert rule.condition["op"] in ("eq", "ne")
            assert isinstance(rule.condition["value"], bool), (
                f"{policy.id}/{rule.id} must decide on a boolean, got "
                f"{rule.condition['value']!r}"
            )
            assert rule.reason.strip(), f"{policy.id}/{rule.id} has no reason"
            checked += 1
    assert checked == 5, f"expected 5 workbook rules, inspected {checked}"
