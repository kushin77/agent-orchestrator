"""The session-fleet steering contract declares the topology and the trust model (issue #161).

The shell gate (``scripts/check-fleet-contract.sh``) is what ``make verify`` runs;
this suite asserts the same declarations inside the per-suite test corpus, and
adds the cross-artifact check that the contract's vocabulary is one the channel
can actually carry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import channel

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "fleet" / "CONTRACT.md"
ADR = ROOT / "docs" / "decision-records" / "ADR-0011-session-fleet-transport.md"
SCHEMA = ROOT / "fleet" / "schema" / "message.schema.json"
STANDING_DIRECTIVE = ROOT / "fleet" / "directive.json"

DIRECTIVE_VERBS = (
    "spawn-epic-agent",
    "dispatch-issue",
    "model-directive",
    "handoff",
    "halt",
    "report",
)

TRUST_RULES = (
    "Only the brain may issue directives to the sister.",
    "The sister may only spawn subagents per a directive.",
    "Subagents report back through the sister.",
    "Everything else is refused.",
)

# The primary-control-plane declaration (issue #763). These phrases exist only in
# the §7.1 subsection — the shell gate pins the same four, so the two halves of
# the gate cannot drift apart.
PRIMARY_CONTROL_PLANE_MARKERS = (
    "A2A is the PRIMARY control plane — not a fallback",
    "à-la-carte reachability invariant",
    "the only authorisation for work",
    "never by silent overwrite",
)


def contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def test_contract_exists():
    assert CONTRACT.is_file(), "the session-fleet contract must exist at fleet/CONTRACT.md"


def test_contract_declares_the_three_roles():
    text = contract_text()
    for role in ("brain", "fleet brain", "sister", "subagent"):
        assert role in text, f"the contract must declare the role '{role}'"


def test_contract_declares_every_directive_verb():
    text = contract_text()
    for verb in DIRECTIVE_VERBS:
        assert verb in text, f"the contract must declare the directive verb '{verb}'"


def test_contract_declares_the_trust_model_verbatim():
    text = contract_text()
    for rule in TRUST_RULES:
        assert rule in text, f"the contract must declare the trust rule '{rule}'"


def test_contract_declares_the_envelope_fields_and_the_finops_block():
    text = contract_text()
    for field in ("correlation_id", "nonce", "from", "to", "model", "thinking"):
        assert field in text, f"the contract must declare the envelope field '{field}'"


def test_contract_points_at_the_authoritative_schema_and_the_transport_adr():
    text = contract_text()
    assert "fleet/schema/message.schema.json" in text
    assert "ADR-0011" in text
    assert SCHEMA.is_file(), "the schema the contract defers to must exist"
    assert ADR.is_file(), "the transport ADR the contract cites must exist"


def test_every_declared_verb_maps_to_a_message_type_the_channel_implements():
    section = contract_text().split("## 2. Directive vocabulary", 1)[1].split("## 3.", 1)[0]
    mapping = dict(
        re.findall(r"^\|\s*`([a-z][a-z-]*)`\s*\|\s*`([a-z]+)`\s*\|", section, re.MULTILINE)
    )
    assert set(mapping) == set(DIRECTIVE_VERBS), (
        "the vocabulary table must declare exactly the six directive verbs"
    )
    for verb, envelope in mapping.items():
        assert envelope in channel.MESSAGE_TYPES, (
            f"verb '{verb}' maps to '{envelope}', which the channel does not implement"
        )


def test_contract_declares_a2a_as_the_primary_control_plane():
    """Issue #763: primary, not a fallback — the word §7's heading got wrong."""
    text = contract_text()
    for marker in PRIMARY_CONTROL_PLANE_MARKERS:
        assert marker in text, f"the contract must declare '{marker}'"
    subsection = text.split("### 7.1 A2A is the PRIMARY control plane", 1)[1].split("\n## 8.", 1)[0]
    for invariant in (
        "dumb terminal",
        "DSv4FNone",
        "never picks its own work",
        "supersede",
        "never by silent overwrite",
        "ARTIFACTS",
        "replaceable",
    ):
        assert invariant in subsection, (
            f"the primary-control-plane subsection must declare '{invariant}'"
        )
    # The declaration is a subsection: §2's table and the numbering above stay put.
    # Provenance is §9, not §8: §7.1 added no section, but issue #727's board-trigger
    # section below it did — the property under test is that §7.1 precedes the
    # terminal Provenance section, so only the digit moves.
    assert "### 7.1" in text and "## 9. Provenance" in text
    assert text.index("### 7.1") < text.index("## 9. Provenance")


def test_the_readme_runbook_defers_to_the_contract():
    runbook = (ROOT / "fleet" / "README.md").read_text(encoding="utf-8")
    assert "CONTRACT.md" in runbook, (
        "fleet/README.md is the runbook and must defer to the normative contract"
    )


def standing_body() -> str:
    return str(json.loads(STANDING_DIRECTIVE.read_text(encoding="utf-8")).get("body") or "")


def test_standing_directive_declares_the_live_cicd_sdlc_clause():
    """The operator's standing order (2026-09-14) is carried in the directive itself."""
    body = standing_body()
    assert "LIVE CI/CD SDLC (standing clause)" in body, (
        "the standing directive must declare the live CI/CD SDLC clause by name"
    )
    for marker in (
        "Verify:",
        "make verify",
        "REAL output",
        "ATOMIC",
        "one issue = one lane = one self-contained, green, reversible commit",
        "ON MERGE",
        "apply pipeline",
        "No agent merges failing or unverified work",
        "GR-15",
        "GitHub Actions",
    ):
        assert marker in body, f"the live CI/CD SDLC clause must declare '{marker}'"


def test_standing_directive_declares_the_replaceability_clause():
    body = standing_body()
    assert "REPLACEABILITY / LIVE INSTRUCTIONS (standing clause)" in body, (
        "the standing directive must declare the replaceability clause by name"
    )
    for marker in (
        "REPLACED",
        "NEW INSTRUCTIONS FRONTLOADED",
        "executes ONLY the directive it was given",
        "never only in the agent's context",
        "TERMINAL",
        "SUPERSEDES",
        "supersedes",
    ):
        assert marker in body, f"the replaceability clause must declare '{marker}'"


def test_standing_directive_carries_the_primary_control_plane_clause():
    """Issue #763: the clause is in the standing order, so every prompt frontloads it."""
    body = standing_body()
    assert "A2A IS THE PRIMARY CONTROL PLANE (standing clause)" in body, (
        "the standing directive must declare the primary-control-plane clause by name"
    )
    for marker in (
        "is the fleet's PRIMARY control plane",
        "the only authorisation for work",
        "NOT an authorisation",
        "à-la-carte",
        "never by silent overwrite",
        "fleet/CONTRACT.md §7.1",
        "docs/OPERATOR-ACCESS.md",
    ):
        assert marker in body, f"the primary-control-plane clause must declare '{marker}'"


def test_the_subagent_prompt_frontloads_the_primary_control_plane_clause():
    """The clause reaches every agent: it rides the standing mandate block.

    The mandate is the prompt's first block, ahead of the order itself
    (`build_prompt` embeds the standing body verbatim), so asserting the clause
    inside the mandate — and ahead of `BRAIN DIRECTIVE` — is the cross-artifact
    edge: the directive declares it AND the prompt carries it, so dropping either
    half fails here rather than shipping silently.
    """
    import sys

    sys.path.insert(0, str(ROOT / "fleet"))
    import terminal  # noqa: E402  (repo convention: fleet/ namespace module)

    prompt = terminal.build_prompt({"id": "d-1", "task": {"issue": 763}, "body": "x"})
    mandate = prompt.split("BRAIN DIRECTIVE", 1)[0]
    assert "A2A IS THE PRIMARY CONTROL PLANE (standing clause)" in mandate, (
        "the standing clause must ride the frontloaded mandate, not the order"
    )
    assert prompt.index("A2A IS THE PRIMARY CONTROL PLANE") < prompt.index("BRAIN DIRECTIVE")


def test_standing_directive_keeps_the_dsv4fnone_and_roles_content():
    """The new clauses are additive: the DSv4FNone order and the roles stay."""
    body = standing_body()
    for marker in (
        "DSv4FNone",
        "DeepSeek v4.1 Flash",
        "dumb terminal",
        "ROLES:",
        "brain = DSv4PM (DeepSeek v4 Pro Max)",
        "fleet/control.py",
    ):
        assert marker in body, f"the standing directive must keep '{marker}'"


def test_the_subagent_prompt_frontloads_the_standing_mandate():
    """The loop's prompt must carry the standing clauses, in its first paragraph.

    This is the cross-artifact edge: the directive declares the mandate AND the
    prompt the loop actually writes frontloads it, so an edit that drops either
    half fails here rather than shipping silently.
    """
    sys_path_bootstrap = ROOT / "fleet"
    import sys

    sys.path.insert(0, str(sys_path_bootstrap))
    import terminal  # noqa: E402  (repo convention: fleet/ namespace module)

    prompt = terminal.build_prompt({"id": "d-1", "task": {"issue": 163}, "body": "x"})
    first_paragraph = prompt.split("\n\n", 1)[0]
    assert "STANDING MANDATE" in first_paragraph
    assert "LIVE CI/CD SDLC" in first_paragraph
    assert "REPLACEABILITY" in first_paragraph
    assert standing_body() in prompt
