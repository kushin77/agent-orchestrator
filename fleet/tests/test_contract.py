"""The session-fleet steering contract declares the topology and the trust model (issue #161).

The shell gate (``scripts/check-fleet-contract.sh``) is what ``make verify`` runs;
this suite asserts the same declarations inside the per-suite test corpus, and
adds the cross-artifact check that the contract's vocabulary is one the channel
can actually carry.
"""

from __future__ import annotations

import re
from pathlib import Path

import channel

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "fleet" / "CONTRACT.md"
ADR = ROOT / "docs" / "decision-records" / "ADR-0011-session-fleet-transport.md"
SCHEMA = ROOT / "fleet" / "schema" / "message.schema.json"

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


def test_the_readme_runbook_defers_to_the_contract():
    runbook = (ROOT / "fleet" / "README.md").read_text(encoding="utf-8")
    assert "CONTRACT.md" in runbook, (
        "fleet/README.md is the runbook and must defer to the normative contract"
    )
