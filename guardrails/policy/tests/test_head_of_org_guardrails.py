"""Head-of-org guardrail policies (issue #951, parent EPIC #878 lanes L9/L10).

Proves the hermes and paperclip policies in
``bundles/platform/hermes-head.yaml`` and
``bundles/platform/paperclip-operator.yaml``:

* every forbidden action is refused **by name** (the matched rule id names
  the forbidden action, not just a generic BLOCK);
* every allowed action passes (LOG, not blocked);
* default-deny holds at both layers — an action this policy does not name
  falls to the policy's own ``default: block``, and an action entirely
  outside the declared scope is ``uncovered`` and falls to the engine's
  fail-closed ``uncovered_decision``;
* every directive hermes issues is audited (the engine's baseline: every
  decision is written to the audit log);
* the default-deny posture: with the two controls OFF, every hermes/
  paperclip action is uncovered and BLOCKed (nothing sails through before a
  reviewed control flip).

The negative control this file makes possible — "removing the hermes policy
file makes the gate red" — is exercised by
``scripts/check-guardrail-head-policy.sh``, not here (a pytest run cannot
delete a source file mid-suite and prove the *gate* reacts; the shell gate
does).
"""

from __future__ import annotations

import pytest

from policy import DecisionLevel, PolicyEngine, build_bundle
from policy.controls import ControlRegistry


@pytest.fixture()
def head_of_org_controls_on(shipped_controls) -> ControlRegistry:
    """A registry with every shipped control registered but only the two
    head-of-org controls flipped ON (the startup gate requires every control
    a bundle's policies reference to be *registered*, even while OFF).

    Every other shipped control stays OFF, so a hermes/paperclip action is
    governed exclusively by its own policy.
    """
    target_ids = {"hermes-head-guardrails", "paperclip-operator-guardrails"}

    def _entry(control) -> dict:
        entry = {
            "id": control.id,
            "name": control.name,
            "description": control.description,
            "enabled": control.id in target_ids,
            "mode": control.mode,
            "implemented_by": list(control.implemented_by),
            "since": control.since,
        }
        if entry["enabled"]:
            entry["on_since_rationale"] = "issue #951 test registry"
        return entry

    return ControlRegistry.from_mapping(
        {"version": 1, "controls": [_entry(control) for control in shipped_controls.all()]}
    )


@pytest.fixture()
def head_engine(shipped_bundle_dir, head_of_org_controls_on) -> PolicyEngine:
    bundle = build_bundle([shipped_bundle_dir], controls=head_of_org_controls_on)
    return PolicyEngine(bundle, controls=head_of_org_controls_on)


# --------------------------------------------------------------------- #
# flag-gated OFF: both controls OFF => uncovered => fail-closed BLOCK
# --------------------------------------------------------------------- #
def test_controls_off_blocks_hermes_and_paperclip_actions(shipped_bundle_dir, shipped_controls):
    # #1953 (owner decision 2026-09-21): hermes-head-guardrails now ships
    # proven ON (#1519) in the shipped registry, so hermes actions are
    # covered/enforced, not uncovered/BLOCK. paperclip-operator-guardrails
    # is still OFF (no closed canary proof yet), so paperclip stays
    # uncovered/BLOCK.
    bundle = build_bundle([shipped_bundle_dir], controls=shipped_controls)
    engine = PolicyEngine(bundle, controls=shipped_controls)

    hermes_result = engine.evaluate(
        "agent.dispatch", subject="hermes", tenant="acme", context={"channel": "gateway"}
    )
    assert hermes_result.decision is DecisionLevel.LOG
    assert hermes_result.uncovered is False

    paperclip_result = engine.evaluate(
        "ticket.update", subject="paperclip", tenant="acme", context={"channel": "gateway"}
    )
    assert paperclip_result.decision is DecisionLevel.BLOCK
    assert paperclip_result.uncovered is True


# --------------------------------------------------------------------- #
# hermes: allowed actions pass
# --------------------------------------------------------------------- #
def test_hermes_dispatch_via_gateway_passes(head_engine):
    result = head_engine.evaluate(
        "agent.dispatch", subject="hermes", tenant="acme", context={"channel": "gateway"}
    )
    assert result.decision is DecisionLevel.LOG
    assert result.matched_rules[0].rule_id == "allow-dispatch-via-gateway"


def test_hermes_model_call_under_ceiling_passes(head_engine):
    result = head_engine.evaluate(
        "model.call",
        subject="hermes",
        tenant="acme",
        context={"budget": {"utilization_ratio": 0.4}},
    )
    assert result.decision is DecisionLevel.LOG
    assert result.matched_rules[0].rule_id == "allow-model-call-under-ceiling"


# --------------------------------------------------------------------- #
# hermes: every forbidden action refused BY NAME
# --------------------------------------------------------------------- #
def test_hermes_directive_off_gateway_refused_by_name(head_engine):
    result = head_engine.evaluate(
        "agent.directive", subject="hermes", tenant="acme", context={"channel": "sideband"}
    )
    assert result.decision is DecisionLevel.BLOCK
    rule_ids = {hit.rule_id for hit in result.matched_rules}
    assert "block-directive-off-gateway" in rule_ids


def test_hermes_self_promote_refused_by_name(head_engine):
    result = head_engine.evaluate("agent.self_promote", subject="hermes", tenant="acme", context={})
    assert result.decision is DecisionLevel.BLOCK
    assert result.matched_rules[0].rule_id == "block-self-promote"


def test_hermes_full_rollout_approval_refused_by_name(head_engine):
    result = head_engine.evaluate(
        "rollout.approve",
        subject="hermes",
        tenant="acme",
        context={"rollout": {"level": "full"}},
    )
    assert result.decision is DecisionLevel.BLOCK
    assert result.matched_rules[0].rule_id == "block-full-rollout-approval"


def test_hermes_partial_rollout_approval_is_not_blocked_by_name(head_engine):
    """A non-full rollout level does not fire the full-rollout block rule
    (it still falls to the policy default: block — default-deny — but not
    via the named forbidden-full-rollout rule)."""
    result = head_engine.evaluate(
        "rollout.approve",
        subject="hermes",
        tenant="acme",
        context={"rollout": {"level": "canary"}},
    )
    rule_ids = {hit.rule_id for hit in result.matched_rules}
    assert "block-full-rollout-approval" not in rule_ids


def test_hermes_secrets_access_refused_by_name(head_engine):
    result = head_engine.evaluate("secrets.access", subject="hermes", tenant="acme", context={})
    assert result.decision is DecisionLevel.BLOCK
    assert result.matched_rules[0].rule_id == "block-secrets-access"


def test_hermes_iac_apply_refused_by_name(head_engine):
    result = head_engine.evaluate("iac.apply", subject="hermes", tenant="acme", context={})
    assert result.decision is DecisionLevel.BLOCK
    assert result.matched_rules[0].rule_id == "block-iac-apply"


def test_hermes_budget_ceiling_exceeded_refused_by_name(head_engine):
    result = head_engine.evaluate(
        "model.call",
        subject="hermes",
        tenant="acme",
        context={"budget": {"utilization_ratio": 1.2}},
    )
    assert result.decision is DecisionLevel.BLOCK
    assert result.matched_rules[0].rule_id == "block-budget-ceiling-exceeded"


# --------------------------------------------------------------------- #
# hermes: default-deny for anything not listed
# --------------------------------------------------------------------- #
def test_hermes_unlisted_action_is_uncovered_and_blocked(head_engine):
    result = head_engine.evaluate("agent.rewrite_history", subject="hermes", tenant="acme", context={})
    assert result.decision is DecisionLevel.BLOCK
    assert result.uncovered is True


# --------------------------------------------------------------------- #
# hermes: every directive is audited
# --------------------------------------------------------------------- #
def test_hermes_directive_is_audited(head_engine):
    head_engine.evaluate("agent.dispatch", subject="hermes", tenant="acme", context={"channel": "gateway"})
    records = list(head_engine.audit_log.records())
    assert records, "expected at least one audit record"
    last = records[-1]
    assert last.action == "agent.dispatch"
    assert last.subject == "hermes"
    assert last.decision == DecisionLevel.LOG.value
    assert "allow-dispatch-via-gateway" in last.rule_ids


def test_hermes_blocked_directive_is_audited(head_engine):
    head_engine.evaluate("agent.self_promote", subject="hermes", tenant="acme", context={})
    records = list(head_engine.audit_log.records())
    last = records[-1]
    assert last.decision == DecisionLevel.BLOCK.value
    assert last.outcome == "blocked"
    assert "block-self-promote" in last.rule_ids


# --------------------------------------------------------------------- #
# paperclip: allowed actions pass
# --------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("action", "rule_id"),
    [
        ("ticket.update", "allow-ticket-update"),
        ("heartbeat.send", "allow-heartbeat"),
        ("budget.query", "allow-budget-query"),
    ],
)
def test_paperclip_contract_action_passes(head_engine, action, rule_id):
    result = head_engine.evaluate(action, subject="paperclip", tenant="acme", context={})
    assert result.decision is DecisionLevel.LOG
    assert result.matched_rules[0].rule_id == rule_id


# --------------------------------------------------------------------- #
# paperclip: every forbidden action refused BY NAME
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("action", ["code.execute", "tool.use", "shell.exec"])
def test_paperclip_code_execution_refused_by_name(head_engine, action):
    result = head_engine.evaluate(action, subject="paperclip", tenant="acme", context={})
    assert result.decision is DecisionLevel.BLOCK
    assert result.matched_rules[0].rule_id == "block-code-execution"


def test_paperclip_cross_tenant_read_refused_by_name(head_engine):
    result = head_engine.evaluate("tenant.read", subject="paperclip", tenant="acme", context={})
    assert result.decision is DecisionLevel.BLOCK
    assert result.matched_rules[0].rule_id == "block-cross-tenant-read"


# --------------------------------------------------------------------- #
# paperclip: default-deny for anything not listed
# --------------------------------------------------------------------- #
def test_paperclip_unlisted_action_is_uncovered_and_blocked(head_engine):
    result = head_engine.evaluate("module.deploy", subject="paperclip", tenant="acme", context={})
    assert result.decision is DecisionLevel.BLOCK
    assert result.uncovered is True


# --------------------------------------------------------------------- #
# scoping: a hermes rule never governs a paperclip subject and vice versa
# --------------------------------------------------------------------- #
def test_hermes_policy_does_not_govern_paperclip_subject(head_engine):
    result = head_engine.evaluate(
        "agent.dispatch", subject="paperclip", tenant="acme", context={"channel": "gateway"}
    )
    assert result.decision is DecisionLevel.BLOCK
    assert result.uncovered is True


def test_paperclip_policy_does_not_govern_hermes_subject(head_engine):
    result = head_engine.evaluate("ticket.update", subject="hermes", tenant="acme", context={})
    assert result.decision is DecisionLevel.BLOCK
    assert result.uncovered is True
