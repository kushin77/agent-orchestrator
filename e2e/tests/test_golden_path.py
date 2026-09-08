"""Golden-path tests (issue #46): the canonical tenant journey, offline.

Every assertion runs the REAL merged pillar modules via the e2e wiring
(identity/onboarding -> identity/rbac -> registry/service -> gateway/proxy
+ providers + finops + health + limits -> guardrails/dlp + policy ->
engine/core -> telemetry/ledger -> telemetry/metering).
"""

from __future__ import annotations

from e2e.golden_path import run_golden_path


def test_signup_provisions_active_tenant_with_seeded_org(control):
    payload = run_golden_path(control)
    signup = payload["stages"]["signup"]
    assert signup["tenant_id"] == "acme"
    assert signup["status"] == "active"
    assert "owner" in signup["roles"]
    assert "admin" in signup["roles"]
    assert signup["seed_count"] > 0  # starter profiles/personas/prompts seeded


def test_rbac_authorizes_owner_and_denies_stranger(control):
    payload = run_golden_path(control)
    rbac = payload["stages"]["rbac"]
    assert rbac["owner_allowed"] is True
    assert rbac["stranger_denied"] is True
    assert rbac["stranger_reason"] == "scope"


def test_agents_registered_active_and_session_scoped(control):
    payload = run_golden_path(control)
    agents = payload["stages"]["agents"]
    assert agents["worker_status"] == "active"
    assert agents["reviewer_status"] == "active"
    assert agents["worker_profile"] == "coder@1.0.0"
    assert agents["session_tenant"] == "acme"


def test_routed_model_call_is_governed_audited_metered(control):
    payload = run_golden_path(control)
    call = payload["stages"]["routed-call"]
    assert call["served"] is True
    assert call["provider"] == "deepseek"
    assert call["outcome"] == "success"
    assert call["policy_decision"] == "log"
    assert call["policy_blocking"] is False
    assert call["dlp_sent"] is True
    assert call["preflight_allowed"] is True
    assert call["ledger_action"] == "model.call"
    assert call["metered"] is True
    assert call["billable"] is True


def test_multi_provider_conformance(control):
    """Same path under DeepSeek / OpenAI / local Ollama / Claude (anthropic)."""
    payload = run_golden_path(control)
    conformance = payload["stages"]["conformance"]
    served = conformance["served"]
    assert {"deepseek", "openai", "ollama", "anthropic"} <= set(served)
    for entry in conformance["providers"]:
        assert entry["served"] is True
        assert entry["policy_decision"] == "log"
        assert entry["dlp_sent"] is True
        assert entry["metered"] is True


def test_durable_engine_run_succeeds_over_real_gateway(control):
    payload = run_golden_path(control)
    durable = payload["stages"]["durable"]
    assert durable["status"] == "succeeded"
    assert durable["new_gateway_records"] == 1  # engine dispatched the gateway


def test_audit_ledger_verifies_ok(control):
    payload = run_golden_path(control)
    audit = payload["stages"]["audit"]
    assert audit["status"] == "OK"
    assert audit["exit_code"] == 0
    # routed call + 4 conformance calls + policy decision are all chained.
    assert audit["records"] >= 6


def test_usage_billing_rolls_up_billable_calls(control):
    payload = run_golden_path(control)
    billing = payload["stages"]["billing"]
    assert billing["billable_calls"] >= 4
    assert billing["total_tokens"] > 0
    assert any("deepseek" in m for m in billing["models"])


def test_all_golden_path_stages_attested(control):
    payload = run_golden_path(control)
    assert payload["attested"] is True
    assert len(payload["attestations"]) >= len(
        [s for s in payload["stages"]]
    )
