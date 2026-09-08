"""Negative-control tests (issue #46): every guard must GENUINELY block.

No-false-green (issue #28): a negative control passes only when the real
guard refuses/denies/fails closed as designed.  A guard that silently passes
when it should block fails the check — proven here per guard, using the real
merged modules offline.
"""

from __future__ import annotations

from honesty import TriState, aggregate

from e2e.negative_controls import NEGATIVE_CHECKS, run_negative_controls

EXPECTED_GUARDS = [
    "cross-tenant-access-denied",
    "dlp-blocks-secret",
    "prompt-injection-blocked",
    "policy-block-enforced",
    "invalid-policy-rejected",
    "budget-breach-blocked",
    "kill-switch-refuses",
    "dead-model-failover",
    "invalid-typed-output-cannot-assess",
    "audit-tamper-detected",
]


def test_every_negative_control_passes(control):
    payload = run_negative_controls(control)
    assert payload["passed"] is True
    assert payload["failed_guards"] == []
    assert set(payload["passed_guards"]) == set(EXPECTED_GUARDS)
    assert len(payload["results"]) == len(NEGATIVE_CHECKS)


def test_cross_tenant_access_denied(control):
    outcome = next(
        r for r in run_negative_controls(control)["results"]
        if r["guard_id"] == "cross-tenant-access-denied"
    )
    assert outcome["rbac_allowed"] is False
    assert outcome["rbac_reason"] == "scope"
    assert outcome["session_denied"] is True
    assert outcome["session_reason"] == "CrossTenantDenied"


def test_dlp_blocks_synthetic_secret(control):
    outcome = next(
        r for r in run_negative_controls(control)["results"]
        if r["guard_id"] == "dlp-blocks-secret"
    )
    assert outcome["verdict"] == "blocked_dlp"
    assert outcome["sent"] is False
    assert any("aws_access_key_id" in reason for reason in outcome["reasons"])


def test_prompt_injection_blocked(control):
    outcome = next(
        r for r in run_negative_controls(control)["results"]
        if r["guard_id"] == "prompt-injection-blocked"
    )
    assert outcome["verdict"] == "blocked_injection"
    assert outcome["sent"] is False


def test_policy_block_enforced(control):
    outcome = next(
        r for r in run_negative_controls(control)["results"]
        if r["guard_id"] == "policy-block-enforced"
    )
    assert outcome["decision"] == "block"
    assert outcome["uncovered"] is False


def test_invalid_policy_rejected(control):
    outcome = next(
        r for r in run_negative_controls(control)["results"]
        if r["guard_id"] == "invalid-policy-rejected"
    )
    assert outcome["rejected"] is True
    assert outcome["reason"] == "PolicyValidationError"


def test_budget_breach_blocked(control):
    outcome = next(
        r for r in run_negative_controls(control)["results"]
        if r["guard_id"] == "budget-breach-blocked"
    )
    assert outcome["allowed"] is False
    assert outcome["decision"] == "block"
    assert outcome["blocking_rail"] == "budget_exceeded"


def test_kill_switch_refuses(control):
    outcome = next(
        r for r in run_negative_controls(control)["results"]
        if r["guard_id"] == "kill-switch-refuses"
    )
    assert outcome["allowed"] is False
    assert outcome["decision"] == "refuse"


def test_dead_model_failover_is_explicit(control):
    outcome = next(
        r for r in run_negative_controls(control)["results"]
        if r["guard_id"] == "dead-model-failover"
    )
    assert outcome["outcome"] == "no_healthy_route"
    assert outcome["served"] is False


def test_invalid_typed_output_is_cannot_assess(control):
    outcome = next(
        r for r in run_negative_controls(control)["results"]
        if r["guard_id"] == "invalid-typed-output-cannot-assess"
    )
    assert outcome["outcome"] == "cannot_assess"
    assert outcome["served"] is False


def test_audit_tamper_detected(control):
    outcome = next(
        r for r in run_negative_controls(control)["results"]
        if r["guard_id"] == "audit-tamper-detected"
    )
    assert outcome["status"] == "NOT-OK"
    assert outcome["exit_code"] == 1
    assert "hash mismatch" in outcome["detail"]


def test_negative_attestations_aggregate_ok(control):
    """The honesty aggregate over every negative guard attestation is OK."""
    payload = run_negative_controls(control)
    statuses = [
        TriState(a["verdict"]["status"]) for a in payload["attestations"]
    ]
    assert statuses, "expected at least one attestation"
    assert aggregate(statuses) is TriState.OK
    assert all(a["exit_code"] == 0 for a in payload["attestations"])
