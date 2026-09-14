"""Mutation / negative controls: the routing and the cap enforcement are not vacuous.

Each test here changes ONE piece of declared policy (in a throwaway copy) and
asserts the OUTCOME moves. If a control were decorative -- a keyword list nobody
reads, a cap nobody enforces, a fail-safe that is only a comment -- the before
and after would be identical and these tests would fail.

This is the same standard the standalone gate ``scripts/check-sme-routing.sh``
applies to itself, in-process.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from smeroute_config import PolicyInvariantViolated, PolicyMalformed, load_bundle
from router import DISPATCHED, HUMAN_ADVISOR, REFUSED, load_router


# --- routing keywords are read ----------------------------------------------
def test_risk_keywords_are_load_bearing(policy_variant) -> None:
    task = {"text": "delete the archived artifacts"}
    before = load_router().route(task)
    assert before.route == "strict"

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["thresholds"]["risk_high_keywords"] = ["zzz-never-matches"]
        return document

    after = load_router(policy_variant(mutate)).route(task)
    assert after.route != before.route
    assert after.tier != before.tier


def test_complexity_keywords_are_load_bearing(policy_variant) -> None:
    task = {"text": "refactor the widget", "tokens": 40}
    before = load_router().route(task)
    assert before.route == "deep"

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["thresholds"]["complexity_keywords"] = ["zzz-never-matches"]
        return document

    after = load_router(policy_variant(mutate)).route(task)
    assert after.route == "fast"


def test_size_thresholds_are_load_bearing(policy_variant) -> None:
    task = {"text": "neutral wording", "tokens": 40}
    assert load_router().route(task).route == "fast"

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["thresholds"]["fast_path_max_tokens"] = 1
        return document

    assert load_router(policy_variant(mutate)).route(task).route == "deep"


def test_sme_domain_keywords_are_load_bearing(policy_variant) -> None:
    text = "rotate the terraform state bucket"
    assert load_router().classify_sme(text) == "terraform"

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["sme_domains"]["infra"]["labels"] = ["zzz-never-matches"]
        return document

    assert load_router(policy_variant(mutate)).classify_sme(text) != "terraform"


def test_squad_keywords_are_load_bearing(policy_variant) -> None:
    text = "secret rotation in the audit trail"
    assert load_router().classify_squad(text) == "security"

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["squads"]["security"]["keywords"] = ["zzz-never-matches"]
        return document

    assert load_router(policy_variant(mutate)).classify_squad(text) != "security"


# --- the fail-safe is a control, not a comment ------------------------------
def test_the_deep_fail_safe_cannot_be_re_pointed_at_the_cheap_path(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["dispatch_defaults"]["unknown_task_type"] = "fast"
        return document

    with pytest.raises(PolicyInvariantViolated):
        load_bundle(policy_variant(mutate))


# --- cap enforcement is real -------------------------------------------------
def test_token_cap_is_what_refuses_the_request(policy_variant) -> None:
    task = {"type": "doc_update", "text": "neutral", "tokens": 9000}
    assert load_router().dispatch(task, escalate=False).status == REFUSED

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            # Raise the whole ladder: otherwise flash->pro would buy no more
            # capacity and the loader would (rightly) refuse the policy.
            document["tiers"]["flash"]["max_tokens"] = 100000
            document["tiers"]["pro"]["max_tokens"] = 200000
            document["tiers"]["auditor"]["max_tokens"] = 300000
        return document

    relaxed = load_router(policy_variant(mutate)).dispatch(task, escalate=False)
    assert relaxed.status == DISPATCHED
    assert relaxed.tier == "flash"


def test_timeout_cap_is_what_refuses_the_request(policy_variant) -> None:
    task = {"text": "neutral", "tokens": 40, "timeout_seconds": 45}
    assert load_router().dispatch(task, escalate=False).status == REFUSED

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            # 60s stays below pro's 120s, so the ladder still buys more capacity.
            document["tiers"]["flash"]["timeout_seconds"] = 60
        return document

    assert load_router(policy_variant(mutate)).dispatch(
        task, escalate=False
    ).status == DISPATCHED


def test_escalation_ladder_is_load_bearing(policy_variant) -> None:
    task = {"type": "doc_update", "text": "neutral", "tokens": 9000}
    before = load_router().dispatch(task)
    assert [attempt.tier for attempt in before.attempts] == ["flash", "pro"]

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["tiers"]["flash"]["fallback"] = "auditor"
        return document

    after = load_router(policy_variant(mutate)).dispatch(task)
    assert [attempt.tier for attempt in after.attempts] == ["flash", "auditor"]
    assert after.tier == "auditor"


def test_the_human_terminal_is_reached_only_because_the_top_tier_has_no_fallback(
    policy_variant,
) -> None:
    task = {"type": "doc_update", "text": "neutral", "tokens": 40000}
    assert load_router().dispatch(task).status == HUMAN_ADVISOR

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            # Give the top tier a bigger cap: the ladder now resolves instead of
            # handing off to a human, so the hand-off was a consequence of the
            # declared caps, not of an unconditional rule.
            document["tiers"]["auditor"]["max_tokens"] = 100000
        return document

    outcome = load_router(policy_variant(mutate)).dispatch(task)
    assert outcome.status == DISPATCHED
    assert outcome.tier == "auditor"


# --- the task-type vocabulary is a single normalised key space --------------
def test_task_override_lookup_survives_the_harvest_spelling_split(policy_variant) -> None:
    task = {"type": "doc_update", "text": "neutral"}
    assert load_router().route(task).tier == "flash"

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            # The hyphenated override is what the hyphenated lookup must find.
            document["task_overrides"]["doc-update"] = "auditor"
        return document

    assert load_router(policy_variant(mutate)).route(task).tier == "auditor"


def test_complexity_range_is_load_bearing(policy_variant) -> None:
    task = {"text": "neutral wording", "tokens": 40, "complexity": 90}
    assert load_router().route(task).tier == "auditor"

    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            # Move the boundary: 90 is now inside the pro band. Still contiguous
            # over 0..100 and still one range per tier, so only the boundary moves.
            document["complexity_to_tier"] = {
                "0-30": "flash",
                "31-90": "pro",
                "91-100": "auditor",
            }
        return document

    assert load_router(policy_variant(mutate)).route(task).tier == "pro"


# --- the schema is a gate ----------------------------------------------------
def test_the_schema_gate_is_load_bearing(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["routes"]["fast"]["model_tier"] = "gpt-5"
        return document

    with pytest.raises(PolicyMalformed):
        load_bundle(policy_variant(mutate))


def test_the_gate_reads_the_shipped_files_only() -> None:
    """A precondition for the mutation tests: nothing above rewrote the real policy."""
    bundle = load_bundle()
    chains: List[Any] = [spec.agents for spec in bundle.route_policy.routes.values()]
    assert ("executor",) in chains
    assert bundle.tier_policy.tier("flash").fallback == "pro"
    assert bundle.route_policy.dispatch_defaults["unknown_task_type"] == "deep"
