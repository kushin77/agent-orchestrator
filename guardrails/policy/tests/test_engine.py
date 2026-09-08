"""Gate-engine decision tests: BLOCK/WARN/LOG, evidence, precedence, audit."""

from __future__ import annotations

import pytest

from policy import (
    DecisionLevel,
    PolicyBundle,
    PolicyEngine,
    strongest,
)
from policy.controls import ControlRegistry

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _empty_engine(**kwargs) -> PolicyEngine:
    return PolicyEngine(PolicyBundle(), **kwargs)


def _deny_list_policy_doc(default: str = "log") -> dict:
    """A deny-list policy: block when the flagged flag is true, else default."""
    return {
        "id": "deny-list",
        "default": default,
        "rules": [
            {
                "id": "block-flagged",
                "actions": ["demo.action"],
                "decision": "block",
                "reason": "flagged for tenant {tenant}",
                "condition": {"path": "demo.flagged", "op": "eq", "value": True},
            }
        ],
    }


def _allow_list_policy_doc() -> dict:
    """An allow-list policy: only 'demo.action' with role admin is allowed."""
    return {
        "id": "allow-list",
        "default": "block",
        "rules": [
            {
                "id": "allow-admin",
                "actions": ["demo.action"],
                "subjects": ["admin"],
                "decision": "log",
                "reason": "admin allowed",
                "condition": {"path": "demo.mode", "op": "eq", "value": "safe"},
            }
        ],
    }


def _engine(policies_docs, controls=None, **kwargs) -> PolicyEngine:
    from policy.loader import policy_from_mapping

    policies = [policy_from_mapping(doc, source="test") for doc in policies_docs]
    return PolicyEngine(PolicyBundle(tuple(policies)), controls=controls or ControlRegistry(), **kwargs)


# ---------------------------------------------------------------------------
# strongest aggregation
# ---------------------------------------------------------------------------


def test_strongest_ordering():
    assert strongest([DecisionLevel.LOG, DecisionLevel.WARN]) is DecisionLevel.WARN
    assert strongest([DecisionLevel.WARN, DecisionLevel.BLOCK]) is DecisionLevel.BLOCK
    assert strongest([DecisionLevel.LOG]) is DecisionLevel.LOG
    assert strongest([]) is None


def test_block_property():
    assert DecisionLevel.BLOCK.blocks is True
    assert DecisionLevel.WARN.blocks is False
    assert DecisionLevel.LOG.blocks is False


# ---------------------------------------------------------------------------
# uncovered actions — fail-closed default
# ---------------------------------------------------------------------------


def test_uncovered_action_blocks_by_default():
    engine = _engine([_deny_list_policy_doc()])
    result = engine.evaluate("other.action")
    assert result.decision is DecisionLevel.BLOCK
    assert result.uncovered is True
    assert result.blocked


def test_uncovered_action_respects_configured_default():
    engine = _engine([_deny_list_policy_doc()], uncovered_decision="log")
    result = engine.evaluate("other.action")
    assert result.decision is DecisionLevel.LOG
    assert result.uncovered is True
    assert not result.blocked


def test_empty_bundle_blocks_everything():
    result = _empty_engine().evaluate("anything.at.all")
    assert result.blocked
    assert result.uncovered is True


# ---------------------------------------------------------------------------
# covered actions — decision levels + evidence
# ---------------------------------------------------------------------------


def test_block_rule_fires_with_evidence(make_policy):
    engine = _engine([_deny_list_policy_doc()])
    result = engine.evaluate(
        "demo.action",
        tenant="acme",
        context={"demo": {"flagged": True}},
    )
    assert result.decision is DecisionLevel.BLOCK
    assert result.uncovered is False
    assert len(result.matched_rules) == 1
    hit = result.matched_rules[0]
    assert hit.policy_id == "deny-list"
    assert hit.rule_id == "block-flagged"
    assert hit.decision is DecisionLevel.BLOCK
    assert "acme" in hit.reason  # {tenant} interpolated
    assert result.policies_consulted == ("deny-list",)


def test_no_rule_fires_uses_policy_default_log():
    engine = _engine([_deny_list_policy_doc()])
    result = engine.evaluate("demo.action", context={"demo": {"flagged": False}})
    assert result.decision is DecisionLevel.LOG
    assert result.matched_rules == ()
    assert result.policies_consulted == ("deny-list",)


def test_warn_rule_returns_warn():
    warn_doc = {
        "id": "warn-only",
        "rules": [
            {
                "id": "warn-when-noisy",
                "actions": ["demo.action"],
                "decision": "warn",
                "reason": "noisy action",
                "condition": {"path": "demo.noisy", "op": "eq", "value": True},
            }
        ],
    }
    engine = _engine([warn_doc])
    result = engine.evaluate("demo.action", context={"demo": {"noisy": True}})
    assert result.decision is DecisionLevel.WARN
    assert not result.blocked


def test_strongest_decision_wins_across_policies():
    warn_policy = {
        "id": "warn-policy",
        "default": "log",
        "rules": [
            {
                "id": "warn-hit",
                "actions": ["demo.action"],
                "decision": "warn",
                "reason": "warn it",
                "condition": {"path": "demo.flagged", "op": "eq", "value": True},
            }
        ],
    }
    engine = _engine([warn_policy, _deny_list_policy_doc()])
    result = engine.evaluate("demo.action", context={"demo": {"flagged": True}})
    assert result.decision is DecisionLevel.BLOCK  # BLOCK beats WARN
    rule_ids = {hit.rule_id for hit in result.matched_rules}
    assert rule_ids == {"warn-hit", "block-flagged"}


def test_allow_list_policy_blocks_unlisted_requests():
    engine = _engine([_allow_list_policy_doc()])
    # non-admin subject: the only rule's subject scope excludes them -> default block
    result = engine.evaluate("demo.action", subject="bob", context={"demo": {"mode": "safe"}})
    assert result.decision is DecisionLevel.BLOCK
    # admin in safe mode -> log (allowed)
    ok = engine.evaluate("demo.action", subject="admin", context={"demo": {"mode": "safe"}})
    assert ok.decision is DecisionLevel.LOG
    # admin in unsafe mode -> default block
    bad = engine.evaluate("demo.action", subject="admin", context={"demo": {"mode": "risky"}})
    assert bad.decision is DecisionLevel.BLOCK


def test_subject_and_tenant_scoping_exclude_rule():
    scoped = {
        "id": "scoped",
        "rules": [
            {
                "id": "tenant-acme-only",
                "actions": ["demo.action"],
                "tenants": ["acme"],
                "subjects": ["agent-1"],
                "decision": "log",
                "reason": "acme agent-1 allowed",
            }
        ],
    }
    engine = _engine([scoped])
    # matching subject+tenant -> LOG (rule fires)
    assert engine.evaluate("demo.action", subject="agent-1", tenant="acme").decision is DecisionLevel.LOG
    # other tenant -> policy does not govern -> uncovered BLOCK (fail closed)
    other = engine.evaluate("demo.action", subject="agent-1", tenant="otherco")
    assert other.decision is DecisionLevel.BLOCK
    assert other.uncovered is True


def test_action_glob_matching():
    glob_doc = {
        "id": "glob",
        "rules": [
            {
                "id": "block-tool-family",
                "actions": ["tool.*"],
                "decision": "block",
                "reason": "tools restricted",
            }
        ],
    }
    engine = _engine([glob_doc])
    assert engine.evaluate("tool.use").decision is DecisionLevel.BLOCK
    assert engine.evaluate("model.call").uncovered is True  # not governed by tool.*


def test_disabled_policy_is_inactive():
    disabled = _deny_list_policy_doc()
    disabled["enabled"] = False
    engine = _engine([disabled])
    result = engine.evaluate("demo.action", context={"demo": {"flagged": True}})
    assert result.uncovered is True
    assert result.decision is DecisionLevel.BLOCK  # uncovered default


def test_reason_with_absent_token_kept_intact():
    doc = {
        "id": "tokens",
        "rules": [
            {
                "id": "block-all",
                "actions": ["demo.action"],
                "decision": "block",
                "reason": "subject {subject} tenant {tenant} plan {context.plan}",
                "condition": {"path": "demo.flagged", "op": "eq", "value": True},
            }
        ],
    }
    engine = _engine([doc])
    result = engine.evaluate("demo.action", tenant="acme", context={"demo": {"flagged": True}})
    reason = result.matched_rules[0].reason
    assert "tenant acme" in reason
    assert "{context.plan}" in reason  # absent token left intact


def test_control_gated_policy_requires_enabled_control(make_policy):
    from policy import Control

    doc = _deny_list_policy_doc()
    doc["controls"] = ["demo-control"]
    registry_off = ControlRegistry([Control("demo-control", "Demo", "d", False, "block", (), "t")])
    registry_on = ControlRegistry([Control("demo-control", "Demo", "d", True, "block", (), "t")])

    engine_off = _engine([doc], controls=registry_off)
    result_off = engine_off.evaluate("demo.action", context={"demo": {"flagged": True}})
    assert result_off.uncovered is True  # gated OFF -> inactive

    engine_on = _engine([doc], controls=registry_on)
    result_on = engine_on.evaluate("demo.action", context={"demo": {"flagged": True}})
    assert result_on.decision is DecisionLevel.BLOCK  # gated ON -> enforced


def test_every_decision_is_audit_logged():
    engine = _engine([_deny_list_policy_doc()])
    assert len(engine.audit_log.records()) == 0
    engine.evaluate("demo.action", context={"demo": {"flagged": True}})  # block
    engine.evaluate("demo.action", context={"demo": {"flagged": False}})  # log
    engine.evaluate("other.action")  # uncovered block
    records = engine.audit_log.records()
    assert len(records) == 3
    outcomes = [record.outcome for record in records]
    assert outcomes.count("blocked") == 2
    assert outcomes.count("allowed") == 1
