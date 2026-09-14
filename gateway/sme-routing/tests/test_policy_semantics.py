"""Semantic-invariant tests: what JSON Schema cannot express, and the tri-state line.

The line this file draws, and keeps:

* ``PolicyMalformed`` / ``PolicyUnavailable`` -> the policy cannot be used to
  evaluate a task at all -> CANNOT-ASSESS (rc 2);
* ``PolicyInvariantViolated`` -> the policy parses and is schema-valid but WRONG
  -> NOT-OK (rc 1).

Every mutation lands in ``tmp_path``; the shipped policies are never rewritten.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from conftest import POLICY_FILES
from smeroute_config import (
    PolicyError,
    PolicyInvariantViolated,
    PolicyMalformed,
    PolicyUnavailable,
    load_bundle,
)


# --- the taxonomy itself -----------------------------------------------------
def test_the_three_failure_classes_are_distinct() -> None:
    assert issubclass(PolicyUnavailable, PolicyError)
    assert issubclass(PolicyMalformed, PolicyError)
    assert issubclass(PolicyInvariantViolated, PolicyError)
    assert not issubclass(PolicyMalformed, PolicyInvariantViolated)
    assert not issubclass(PolicyInvariantViolated, PolicyMalformed)


# --- the shipped policy satisfies every invariant ---------------------------
def test_shipped_policy_loads_and_is_internally_consistent() -> None:
    bundle = load_bundle()
    assert bundle.route_policy.normalised_defaults["unknown-task-type"] == "deep"
    assert bundle.tier_policy.tier_order == ("flash", "pro", "auditor")
    assert bundle.tier_policy.tier(bundle.tier_policy.terminal_tier).fallback is None
    assert bundle.capability_registry.sme_default == (
        bundle.capability_registry.sme_domains["general"].sme
    )


# --- the fail-safe cannot drift ---------------------------------------------
def test_unknown_task_fail_safe_must_stay_deep(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["dispatch_defaults"]["unknown_task_type"] = "fast"
        return document

    with pytest.raises(PolicyInvariantViolated, match="unknown_task_type must be 'deep'"):
        load_bundle(policy_variant(mutate))


def test_two_dispatch_defaults_colliding_after_normalisation_are_refused(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["dispatch_defaults"]["doc-update"] = "deep"
        return document

    with pytest.raises(PolicyInvariantViolated, match="collides with"):
        load_bundle(policy_variant(mutate))


def test_two_task_overrides_colliding_after_normalisation_are_refused(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["task_overrides"]["doc_update"] = "pro"
        return document

    with pytest.raises(PolicyInvariantViolated, match="collides with"):
        load_bundle(policy_variant(mutate))


def test_threshold_ordering_must_be_unambiguous(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["thresholds"]["fast_path_max_tokens"] = 500
        return document

    with pytest.raises(PolicyInvariantViolated, match="must be below"):
        load_bundle(policy_variant(mutate))


def test_dispatch_default_must_name_a_declared_route(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["dispatch_defaults"]["audit"] = "sideways"
        return document

    with pytest.raises(PolicyMalformed):
        load_bundle(policy_variant(mutate))


# --- referential integrity ---------------------------------------------------
def test_route_chain_must_name_declared_agents(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["routes"]["deep"]["agents"].append("wizard")
        return document

    with pytest.raises(PolicyInvariantViolated, match="wizard"):
        load_bundle(policy_variant(mutate))


def test_route_worker_fleet_must_be_declared(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "route-policy.yaml":
            document["routes"]["fast"]["worker_types"] = ["ghost-fleet"]
        return document

    with pytest.raises(PolicyInvariantViolated, match="ghost-fleet"):
        load_bundle(policy_variant(mutate))


def test_agent_worker_type_must_be_declared(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["agents"][0]["worker_types"] = ["ghost-fleet"]
        return document

    with pytest.raises(PolicyInvariantViolated, match="ghost-fleet"):
        load_bundle(policy_variant(mutate))


def test_agent_fallback_must_name_a_declared_agent(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["agents"][1]["fallback"] = ["nobody"]
        return document

    with pytest.raises(PolicyInvariantViolated, match="nobody"):
        load_bundle(policy_variant(mutate))


def test_worker_fleet_role_must_be_declared(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["worker_fleet"]["code-fleet"]["agent_roles"] = ["intern"]
        return document

    with pytest.raises(PolicyInvariantViolated, match="intern"):
        load_bundle(policy_variant(mutate))


def test_dispatch_chain_must_name_declared_agents(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["dispatch_routing"]["audit_chain"] = ["planner", "ghost"]
        return document

    with pytest.raises(PolicyInvariantViolated, match="ghost"):
        load_bundle(policy_variant(mutate))


def test_task_override_must_name_a_declared_tier(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["task_overrides"]["audit"] = "quantum"
        return document

    with pytest.raises(PolicyInvariantViolated, match="quantum"):
        load_bundle(policy_variant(mutate))


def test_tier_fallback_must_be_declared(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["tiers"]["pro"]["fallback"] = "platinum"
        return document

    with pytest.raises(PolicyInvariantViolated, match="platinum"):
        load_bundle(policy_variant(mutate))


def test_complexity_map_must_name_declared_tiers(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["complexity_to_tier"]["0-30"] = "micro"
        return document

    with pytest.raises(PolicyInvariantViolated, match="micro"):
        load_bundle(policy_variant(mutate))


def test_squad_default_must_be_declared(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["squad_default"] = "platypus"
        return document

    with pytest.raises(PolicyInvariantViolated, match="platypus"):
        load_bundle(policy_variant(mutate))


def test_domain_module_must_be_in_the_authority_matrix(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["sme_domains"]["infra"]["module"] = "vendor/imaginary"
        return document

    with pytest.raises(PolicyInvariantViolated, match="vendor/imaginary"):
        load_bundle(policy_variant(mutate))


def test_a_domain_with_no_label_is_dead_data(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["sme_domains"]["infra"]["labels"] = []
        return document

    with pytest.raises(PolicyInvariantViolated, match="no label"):
        load_bundle(policy_variant(mutate))


def test_the_default_squad_must_be_the_keywordless_arm(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["squads"]["shell"]["keywords"] = ["shell"]
        return document

    with pytest.raises(PolicyInvariantViolated, match="keyword-less"):
        load_bundle(policy_variant(mutate))


def test_sme_default_must_match_the_general_domain(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document["sme_default"] = "unrelated"
        return document

    with pytest.raises(PolicyInvariantViolated, match="sme_default"):
        load_bundle(policy_variant(mutate))


# --- the escalation ladder ---------------------------------------------------
def test_the_terminal_tier_must_declare_a_null_fallback(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["tiers"]["auditor"]["fallback"] = "pro"
        return document

    with pytest.raises(PolicyInvariantViolated, match="must terminate at null"):
        load_bundle(policy_variant(mutate))


def test_a_cyclic_ladder_is_refused(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["tiers"]["flash"]["fallback"] = "flash"
        return document

    with pytest.raises(PolicyInvariantViolated, match="cycles through"):
        load_bundle(policy_variant(mutate))


def test_ladder_must_ascend_with_the_declared_tier_order(policy_variant) -> None:
    # Declare the complexity ranges in the reverse tier order while the
    # escalation ladder still climbs flash -> auditor: the two orders disagree,
    # so the "higher tier" the router escalates to is not the more capable one.
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["complexity_to_tier"] = {
                "0-30": "auditor",
                "31-70": "pro",
                "71-100": "flash",
            }
            document["tiers"]["flash"]["fallback"] = None
        return document

    with pytest.raises(PolicyInvariantViolated, match="strictly increasing"):
        load_bundle(policy_variant(mutate))


def test_escalation_must_buy_more_capacity(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["tiers"]["pro"]["max_tokens"] = 100
        return document

    with pytest.raises(PolicyInvariantViolated, match="buys no more capacity"):
        load_bundle(policy_variant(mutate))


def test_each_rung_is_strictly_more_capable_than_the_last() -> None:
    bundle = load_bundle()
    for name in bundle.tier_policy.tier_order:
        spec = bundle.tier_policy.tier(name)
        if spec.fallback is None:
            continue
        higher = bundle.tier_policy.tier(spec.fallback)
        assert higher.max_tokens > spec.max_tokens
        assert higher.timeout_seconds > spec.timeout_seconds


# --- the complexity map ------------------------------------------------------
def test_a_complexity_gap_is_cannot_assess(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["complexity_to_tier"]["41-70"] = (
                document["complexity_to_tier"].pop("31-70")
            )
        return document

    with pytest.raises(PolicyMalformed, match="not contiguous"):
        load_bundle(policy_variant(mutate))


def test_a_complexity_map_that_stops_before_100_is_cannot_assess(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["complexity_to_tier"]["71-99"] = (
                document["complexity_to_tier"].pop("71-100")
            )
        return document

    with pytest.raises(PolicyMalformed, match="must cover 0..100"):
        load_bundle(policy_variant(mutate))


def test_two_ranges_on_one_tier_are_ambiguous_and_refused(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["complexity_to_tier"]["71-100"] = "flash"
        return document

    with pytest.raises(PolicyMalformed, match="same tier"):
        load_bundle(policy_variant(mutate))


def test_an_inverted_range_is_refused(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            del document["complexity_to_tier"]["31-70"]
            document["complexity_to_tier"]["70-31"] = "pro"
        return document

    with pytest.raises(PolicyMalformed, match="inverted"):
        load_bundle(policy_variant(mutate))


def test_non_range_key_is_refused(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["complexity_to_tier"]["easy"] = "flash"
        return document

    with pytest.raises(PolicyMalformed):
        load_bundle(policy_variant(mutate))


# --- malformed / unavailable inputs -----------------------------------------
def test_missing_required_field_is_cannot_assess(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "capability-registry.yaml":
            document.pop("agents")
        return document

    with pytest.raises(PolicyMalformed, match="missing required property 'agents'"):
        load_bundle(policy_variant(mutate))


def test_wrong_type_is_cannot_assess(policy_variant) -> None:
    def mutate(filename: str, document: Dict[str, Any]) -> Dict[str, Any]:
        if filename == "tier-policy.yaml":
            document["tiers"]["flash"]["max_tokens"] = "big"
        return document

    with pytest.raises(PolicyMalformed):
        load_bundle(policy_variant(mutate))


def test_a_missing_policy_directory_is_unavailable(tmp_path: Path) -> None:
    with pytest.raises(PolicyUnavailable, match="policy directory not found"):
        load_bundle(tmp_path / "absent")


def test_a_missing_policy_file_is_unavailable(policy_variant) -> None:
    variant = policy_variant()
    (variant / "route-policy.yaml").unlink()
    with pytest.raises(PolicyUnavailable, match="not found"):
        load_bundle(variant)


def test_unparseable_yaml_is_cannot_assess(policy_variant) -> None:
    variant = policy_variant()
    (variant / "tier-policy.yaml").write_text("tiers: [oops\n", encoding="utf-8")
    with pytest.raises(PolicyMalformed, match="not valid YAML"):
        load_bundle(variant)


def test_a_non_mapping_document_is_cannot_assess(policy_variant) -> None:
    variant = policy_variant()
    (variant / "route-policy.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(PolicyMalformed, match="top level must be a mapping"):
        load_bundle(variant)


def test_every_declared_policy_file_is_required(policy_variant) -> None:
    for filename in POLICY_FILES:
        variant = policy_variant(name=f"missing-{filename}")
        (variant / filename).unlink()
        with pytest.raises(PolicyUnavailable):
            load_bundle(variant)


def test_the_policies_directory_override_is_honoured(policy_variant) -> None:
    variant = policy_variant()
    bundle = load_bundle(variant)
    assert bundle.policies_dir == variant
    assert bundle.route_policy.version == load_bundle().route_policy.version
