"""Persona -> AgentProfile mapping (issue #11 bridge to the issue #9 contract)."""

import pytest

import mapping
import registry

REAL = registry.PersonaRegistry()


def test_materialize_selects_the_contract_fields(scratch):
    reg = scratch.write_registry(
        {
            "alpha.yaml": scratch.card_yaml(
                id="alpha",
                tenant="acme",
                defaultModelTier="MED",
                guardrailPolicyRef="worker-bundle",
                systemPromptRef="alpha/primary@v1",
            )
        }
    )
    card = reg.get("acme", "alpha")
    profile = mapping.materialize_profile(card)
    # The persona *selects* prompt/tools/tier/guardrails; owner is derived.
    assert profile["systemPromptRef"] == "alpha/primary@v1"
    assert profile["defaultModelTier"] == "MED"
    assert profile["guardrailPolicyRef"] == "worker-bundle"
    assert profile["toolAllowlist"] == ["file_read", "shell_exec"]
    assert profile["owner"] == "acme/alpha"
    assert profile["id"] == "alpha"
    assert profile["version"] == "1.0.0"
    mapping.validate_profile(profile)


def test_owner_is_derived_from_tenant_scope():
    card = registry.card_from_yaml(registry.CARDS_DIR / "coder.yaml")
    profile = mapping.materialize_profile(card, tenant="platform")
    assert profile["owner"] == "platform/coder"
    tenant_profile = mapping.materialize_profile(card, tenant="acme")
    assert tenant_profile["owner"] == "acme/coder"


def test_orchestrator_profile_parity_with_seed():
    """The orchestrator persona maps onto the issue #9 orchestrator seed."""
    card = registry.card_from_yaml(registry.CARDS_DIR / "orchestrator.yaml")
    profile = mapping.materialize_profile(card)
    assert profile["id"] == "orchestrator"
    assert profile["systemPromptRef"] == "orchestrator/primary@v1"
    assert profile["defaultModelTier"] == "MED"
    assert profile["guardrailPolicyRef"] == "worker-bundle"
    assert profile["capabilitySet"] == ["orchestrate", "task-claim", "research", "memory-ops"]
    assert profile["toolAllowlist"] == [
        "gh_issue",
        "board_sync",
        "git_worktree",
        "file_read",
        "shell_exec",
        "search_memory",
        "store_memory",
    ]


def test_materialize_rejects_unknown_vocabulary():
    """The profile layer itself is fail-closed against the live catalog."""
    card = dict(registry.card_from_yaml(registry.CARDS_DIR / "coder.yaml"))
    card["toolAllowlist"] = list(card["toolAllowlist"]) + ["not_a_tool"]
    with pytest.raises(mapping.ProfileMaterializationError):
        mapping.materialize_profile(card)


def test_all_seed_personas_materialize():
    """Every shipped persona produces a schema-valid AgentProfile."""
    cards = REAL.discover()
    assert len(cards) == 15
    for (_tenant, _persona_id), card in cards.items():
        profile = mapping.materialize_profile(card)
        mapping.validate_profile(profile)


def test_profile_validation_requires_all_ten_fields():
    """Dropping a profile-required field fails the issue #9 contract."""
    profile = mapping.materialize_profile(
        registry.card_from_yaml(registry.CARDS_DIR / "coder.yaml")
    )
    del profile["memoryScope"]
    with pytest.raises(mapping.ProfileMaterializationError):
        mapping.validate_profile(profile)
