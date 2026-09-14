"""Behavioural routing tests: route, chain, tier and SME classification.

These assert on OUTCOMES (route/tier/chain/status), never on source text, so a
refactor that keeps behaviour keeps the suite green while a behaviour change
cannot hide.
"""

from __future__ import annotations

from typing import Any

import pytest

from router import TaskInvalid


# --- the three demonstrated dispatch paths ----------------------------------
def test_fast_path_routes_to_the_cheap_single_agent_chain(router) -> None:
    decision = router.route(
        {"type": "doc_update", "text": "tighten the README wording", "tokens": 40}
    )
    assert decision.route == "fast"
    assert decision.path_mode == "fast"
    assert decision.tier == "flash"
    assert decision.chain == ("executor",)
    assert decision.fail_safe is False
    assert decision.caps.max_tokens == 4096
    assert decision.caps.timeout_seconds == 30


def test_deep_path_routes_to_the_plan_execute_verify_chain(router) -> None:
    decision = router.route(
        {"text": "refactor the loader across multi-file modules", "tokens": 900}
    )
    assert decision.route == "deep"
    assert decision.path_mode == "deep"
    assert decision.tier == "pro"
    assert decision.chain == ("planner", "executor", "verifier")
    assert decision.worker_types == ("code-fleet", "scout-fleet", "scribe-fleet")


def test_strict_path_routes_to_the_governance_chain(router) -> None:
    decision = router.route({"text": "rotate the production secret credential"})
    assert decision.route == "strict"
    assert decision.path_mode == "deep"
    assert decision.tier == "auditor"
    assert decision.chain == ("planner", "executor", "verifier", "critic")
    assert decision.caps.timeout_seconds == 300
    assert decision.caps.fallback is None


# --- the fail-safe ----------------------------------------------------------
def test_unknown_task_type_defaults_to_the_deep_path(router) -> None:
    decision = router.route(
        {"type": "teleport", "text": "do the undeclared thing", "tokens": 10}
    )
    assert decision.route == "deep"
    assert decision.fail_safe is True
    assert decision.chain == ("planner", "executor", "verifier")


def test_no_signal_defaults_to_the_deep_path(router) -> None:
    decision = router.route({"text": "quarterly ledger reconciliation"})
    assert decision.route == "deep"
    assert decision.fail_safe is True


def test_the_fail_safe_is_declared_data_not_a_hard_coded_string(router) -> None:
    assert router.routes.normalised_defaults["unknown-task-type"] == "deep"
    assert router.routes.dispatch_defaults["unknown_task_type"] == "deep"


def test_an_unrecognised_type_beats_the_size_heuristic(router) -> None:
    # A tiny token estimate would take the fast path -- an undeclared type must not.
    decision = router.route({"type": "teleport", "text": "x", "tokens": 5})
    assert decision.route == "deep"
    assert decision.fail_safe is True


# --- the signals genuinely change the outcome -------------------------------
def test_risk_keyword_overrides_a_declared_fast_type(router) -> None:
    declared = router.route({"type": "doc_update", "text": "document the changelog"})
    risky = router.route(
        {"type": "doc_update", "text": "document the production secret rotation"}
    )
    assert declared.route == "fast"
    assert risky.route == "strict"
    assert declared.tier != risky.tier


def test_explicit_high_risk_routes_strict(router) -> None:
    decision = router.route({"text": "tidy the changelog", "risk": "high"})
    assert decision.route == "strict"


def test_complexity_keyword_overrides_the_size_heuristic(router) -> None:
    plain = router.route({"text": "touch the widget", "tokens": 40})
    complex_task = router.route({"text": "refactor the widget", "tokens": 40})
    assert plain.route == "fast"
    assert complex_task.route == "deep"
    assert complex_task.fail_safe is False


def test_token_thresholds_change_the_route(router) -> None:
    small = router.route({"text": "neutral wording", "tokens": 40})
    large = router.route({"text": "neutral wording", "tokens": 900})
    assert small.route == "fast"
    assert large.route == "deep"
    assert large.fail_safe is False


def test_declared_type_mapping_is_read_from_the_policy(router) -> None:
    fast = router.route({"type": "doc_update", "text": "neutral"})
    strict = router.route({"type": "code_review", "text": "neutral"})
    assert fast.route == "fast"
    assert strict.route == "strict"
    assert router.routes.normalised_defaults["code-review"] == "strict"


# --- tier selection ---------------------------------------------------------
def test_complexity_score_raises_the_model_tier(router) -> None:
    base = {"text": "neutral wording", "tokens": 40}
    low = router.route(dict(base, complexity=10))
    mid = router.route(dict(base, complexity=50))
    high = router.route(dict(base, complexity=90))
    assert low.tier == "flash"
    assert mid.tier == "pro"
    assert high.tier == "auditor"


def test_complexity_never_de_escalates_below_the_route_tier(router) -> None:
    decision = router.route({"text": "production secret", "complexity": 5})
    assert decision.route == "strict"
    assert decision.tier == "auditor"


def test_task_override_raises_the_tier(router) -> None:
    decision = router.route({"type": "code_review", "text": "review the diff"})
    assert decision.route == "strict"
    assert decision.tier == "auditor"
    assert decision.caps.max_tokens == 32768


def test_underscore_and_hyphen_task_types_resolve_to_the_same_override(router) -> None:
    # route-policy spells `doc_update`; tier-policy spells `doc-update`.
    underscore = router.route({"type": "doc_update", "text": "neutral"})
    hyphen = router.route({"type": "doc-update", "text": "neutral"})
    assert underscore.route == hyphen.route == "fast"
    assert underscore.tier == hyphen.tier == "flash"
    assert "task override" in underscore.reason


def test_tier_order_is_the_declared_complexity_order(router) -> None:
    assert router.tiers.tier_order == ("flash", "pro", "auditor")
    assert router.tiers.terminal_tier == "auditor"


# --- SME / domain / squad classification ------------------------------------
@pytest.mark.parametrize(
    "text,expected_domain,expected_sme",
    (
        ("rotate the production secret credential", "security-scanning", "security"),
        ("rotate the terraform state bucket", "infra", "terraform"),
        ("add a vitest coverage check", "testing", "test-quality"),
        ("update the readme documentation", "docs", "sniper-generic"),
        ("touch the prisma middleware", "backend-server", "prisma-db"),
        ("triage the crash investigation", "debugging", "sniper-generic"),
        ("tidy the portal css", "frontend-vibe", "sniper-generic"),
    ),
)
def test_domain_classification_routes_to_the_declared_sme(
    router, text: str, expected_domain: str, expected_sme: str
) -> None:
    assert router.classify_domain(text) == expected_domain
    assert router.classify_sme(text) == expected_sme


def test_unmatched_text_falls_to_the_declared_general_sme(router) -> None:
    assert router.classify_domain("quarterly ledger reconciliation") == "general"
    assert router.classify_sme("quarterly ledger reconciliation") == (
        router.registry.sme_default
    )


def test_label_matching_is_substring_like_the_harvest(router) -> None:
    # The harvested classifier greps each label as a substring, so a short label
    # matches inside a word ("ui" in "quick"). The port keeps that behaviour
    # rather than inventing word-boundary semantics the source does not have.
    assert router.classify_domain("the quick brown fox") == "frontend-vibe"


@pytest.mark.parametrize(
    "text,expected_squad",
    (
        ("secret rotation in the audit trail", "security"),
        ("docker compose rollout plan", "deploy"),
        ("add a coverage gate", "testing"),
        ("architecture strategy memo", "staff"),
        ("neutral wording with no keyword", "shell"),
    ),
)
def test_squad_classification(router, text: str, expected_squad: str) -> None:
    assert router.classify_squad(text) == expected_squad


def test_squad_tie_resolves_to_the_first_declared_match(router) -> None:
    # `secret` (security) and `test` (testing) both match; security is declared first.
    assert router.classify_squad("a secret in the test gate") == "security"
    assert list(router.registry.squads)[0] == "security"


def test_module_authority_lookup(router) -> None:
    assert router.route({"text": "rotate the terraform state bucket"}).module == (
        "vendor/gcp-gatekeeper"
    )
    assert router.route({"text": "update the readme"}).module == "vendor/mxdocs"
    assert router.route({"text": "quarterly ledger reconciliation"}).module == ""


def test_classify_returns_the_full_triple(router) -> None:
    classified = router.classify("rotate the terraform state bucket")
    assert classified["domain"] == "infra"
    assert classified["sme"] == "terraform"
    assert classified["module"] == "vendor/gcp-gatekeeper"
    assert classified["squad"] in router.registry.squads


# --- determinism and the request envelope -----------------------------------
def test_routing_is_deterministic(router) -> None:
    task = {"type": "doc_update", "text": "tighten the README wording", "tokens": 40}
    first = router.route(task).as_dict()
    second = router.route(task).as_dict()
    assert first == second


def test_decision_serialises_to_json_shape(router) -> None:
    payload = router.route({"text": "neutral"}).as_dict()
    assert set(payload) == {
        "task_id",
        "route",
        "path_mode",
        "tier",
        "chain",
        "worker_types",
        "domain",
        "sme",
        "squad",
        "module",
        "fail_safe",
        "reason",
        "caps",
    }
    assert set(payload["caps"]) == {
        "tier",
        "models",
        "timeout_seconds",
        "max_tokens",
        "fallback",
    }


def test_task_id_is_echoed(router) -> None:
    assert router.route({"id": "T-42", "text": "neutral"}).task_id == "T-42"


def test_metadata_is_the_declared_escape_hatch(router) -> None:
    decision = router.route({"text": "neutral", "metadata": {"trace": "abc"}})
    assert decision.route == "deep"


@pytest.mark.parametrize(
    "task",
    (
        {"text": "x", "token": 5},
        {"text": "x", "complexity": 150},
        {"text": "x", "complexity": "high"},
        {"text": "x", "risk": "urgent"},
        {"text": "x", "tokens": -1},
        {"text": 7},
        {"text": "x", "type": 5},
        "not-a-mapping",
    ),
)
def test_an_unusable_request_is_refused_not_guessed(router, task: Any) -> None:
    with pytest.raises(TaskInvalid):
        router.route(task)


def test_text_is_optional(router) -> None:
    decision = router.route({})
    assert decision.route == "deep"
    assert decision.fail_safe is True


def test_route_never_returns_an_undeclared_chain(router) -> None:
    for route_name, spec in router.routes.routes.items():
        for role in spec.agents:
            assert role in router.registry.agent_ids
        assert spec.model_tier in router.tiers.tiers
        assert route_name in ("fast", "deep", "strict")


def test_reason_names_the_signal_that_decided(router) -> None:
    risky = router.route({"text": "delete the production database"})
    assert "risk keyword" in risky.reason
    unknown = router.route({"type": "teleport", "text": "x"})
    assert "fail-safe" in unknown.reason


def test_caps_are_copied_from_the_declared_tier(router) -> None:
    for task in (
        {"type": "doc_update", "text": "neutral"},
        {"text": "refactor neutral"},
        {"text": "production secret"},
        {"text": "neutral", "complexity": 90},
    ):
        decision = router.route(task)
        spec = router.tiers.tier(decision.tier)
        assert decision.caps.tier == spec.name
        assert decision.caps.models == spec.models
        assert decision.caps.max_tokens == spec.max_tokens
        assert decision.caps.timeout_seconds == spec.timeout_seconds
        assert decision.caps.fallback == spec.fallback


def test_registry_and_route_views_are_exposed(router) -> None:
    assert "executor" in router.registry.agent_ids
    assert "auditor-fleet" in router.registry.worker_fleet_names
    assert router.registry.dispatch_routing["governance_chain"] == (
        "planner",
        "executor",
        "verifier",
        "critic",
    )
    assert isinstance(router.routes.routes, dict)
