"""Seed persona cards validate, discover and carry the expected mix.

Covers acceptance: seed personas assembled from the harvested library
(file-per-persona, filename-adds-no-code-change), including the auditor
persona, each carrying provenance per the repo provenance rule (GR-10).
"""

from pathlib import Path

import registry
import mapping

PKG = Path(__file__).resolve().parents[1]  # registry/personas
CARDS_DIR = PKG / "cards"
REAL = registry.PersonaRegistry()

EXPECTED_IDS = {
    "orchestrator",
    "coder",
    "researcher",
    "data-agent",
    "debugging-sme",
    "docs-author",
    "sniper",
    "reviewer",
    "security-sme",
    "iac-sme",
    "qa-sme",
    "architecture-sme",
    "frontend-sme",
    "docs-sme",
    "auditor",
    "ollama",
    "paperclip",
    "hermes",
    "deepseek",
    "claude",
    "platform-sme",
    "pmo-sme",
    "sync-sme",
    "gcp-gatekeeper-sme",
    "mechanical-sme",
    "copilot",
    # C-suite org chart (issue #632, workbook-1)
    "ceo",
    "cto",
    "coo",
    "cfo",
    "cmo",
}


def test_seed_card_count_and_auditor_present():
    files = sorted(CARDS_DIR.glob("*.yaml"))
    assert len(files) == len(EXPECTED_IDS) == 31
    assert {f.stem for f in files} == EXPECTED_IDS


def test_seed_cards_validate_and_match_filename_stem():
    cards = REAL.discover()
    assert len(cards) == 31
    for (tenant, persona_id), card in cards.items():
        assert tenant == "platform"  # every seed is a platform-default persona
        assert persona_id == card["id"]


def test_every_card_records_provenance():
    cards = REAL.discover()
    for (_tenant, persona_id), card in cards.items():
        assert card["provenance"], f"{persona_id} must record provenance (GR-10)"


def test_seed_posture_mix():
    """Executors execute, reviewers review, and exactly one auditor ships."""
    cards = REAL.discover()
    postures = {}
    for (_tenant, _persona_id), card in cards.items():
        postures[card["posture"]] = postures.get(card["posture"], 0) + 1
    # The five C-suite cards (issue #632) are all executors: 16 + 5 = 21.
    assert postures == {"auditor": 1, "executor": 21, "reviewer": 9}
    assert any(c["posture"] == "auditor" for c in cards.values())


def test_reviewer_personas_carry_verify_only_toolset():
    """Reviewer/auditor personas never carry file_write in their allowlist."""
    cards = REAL.discover()
    for (_tenant, _persona_id), card in cards.items():
        if card["posture"] in ("reviewer", "auditor"):
            assert "file_write" not in card["toolAllowlist"]
            assert "shell_exec" in card["toolAllowlist"]  # they run gates


def test_every_seed_materializes_to_a_valid_profile():
    """Cross-contract guarantee: all 25 cards map onto issue #9 AgentProfiles."""
    cards = REAL.discover()
    for (_tenant, persona_id), card in cards.items():
        profile = mapping.materialize_profile(card)
        mapping.validate_profile(profile)
        assert profile["id"] == persona_id
        assert set(profile) == {
            "id",
            "version",
            "owner",
            "systemPromptRef",
            "toolAllowlist",
            "constraintSet",
            "capabilitySet",
            "defaultModelTier",
            "memoryScope",
            "guardrailPolicyRef",
        }
