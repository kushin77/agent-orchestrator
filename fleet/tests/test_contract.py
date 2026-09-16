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
    "Only the director may issue directives to the dispatcher.",
    "The dispatcher may only spawn executors per a directive.",
    "Executors report back through the dispatcher.",
    "Everything else is refused.",
)

# The RETIRED (schema 1) rendering of the same four rules. Schema 1 is still
# ACCEPTED on read for the deprecation window the glossary declares, so the
# contract must go on declaring the exact rules a schema-1 reader enforces — as a
# glow, inside the marked legacy region. `scripts/check-fleet-contract.sh` pins
# these same four strings, so keeping them is the contract's own obligation and
# not only this suite's.
RETIRED_TRUST_RULES = (
    "Only the brain may issue directives to the sister.",
    "The sister may only spawn subagents per a directive.",
    "Subagents report back through the sister.",
    "Everything else is refused.",
)

#: The role vocabulary (issue #777). The single authority is
#: `governance/vocabulary/fleet.yaml`; the contract renders it, and the marked
#: legacy region is the ONLY place a retired term may appear as a name.
CURRENT_ROLES = ("principal", "director", "dispatcher", "executor")
RETIRED_ROLES = ("operator", "brain", "sister", "subagent")
LEGACY_START = "<!-- legacy-gloss:start -->"
LEGACY_END = "<!-- legacy-gloss:end -->"


def legacy_region(text: str) -> str:
    """Every marked legacy-gloss region, concatenated."""
    parts: list[str] = []
    cursor = 0
    while True:
        opening = text.find(LEGACY_START, cursor)
        closing = text.find(LEGACY_END, opening + 1) if opening >= 0 else -1
        if opening < 0 or closing < 0:
            return "\n".join(parts)
        parts.append(text[opening : closing + len(LEGACY_END)])
        cursor = closing + len(LEGACY_END)


def outside_legacy_region(text: str) -> str:
    """The contract with every marked legacy-gloss region removed."""
    while LEGACY_START in text and LEGACY_END in text:
        opening = text.index(LEGACY_START)
        closing = text.index(LEGACY_END, opening) + len(LEGACY_END)
        text = text[:opening] + text[closing:]
    return text


def prose_only(text: str) -> str:
    """The text a retired term may NOT appear in: everything but the artifacts.

    Mirrors the rule `scripts/check-fleet-vocabulary.sh` enforces — an inline code
    span, a link target, a path and a filename are ARTIFACT NAMES (the glossary's
    declared out-of-scope), so a retired word inside one is named machinery rather
    than a role. §3 legitimately renders the schema-1 dialect in code spans.
    """
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\]\([^)]*\)", " ", text)
    text = re.sub(r"\S*/\S*", " ", text)
    return re.sub(r"[\w.-]+\.(?:py|sh|json|md|log|lock|ya?ml|txt)\b", " ", text).lower()

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


def test_contract_declares_the_four_roles_in_the_current_vocabulary():
    """§1 names the function, not the metaphor (issue #777)."""
    section = contract_text().split("## 1. Roles", 1)[1].split("## 2.", 1)[0]
    for role in CURRENT_ROLES:
        assert role in section, f"§1 must declare the role '{role}'"
    assert "the dispatcher never picks work" in section.lower(), (
        "§1 must keep the invariant the retired metaphor carried"
    )


def test_the_retired_spellings_survive_as_a_declared_gloss_only():
    """Dual-accept on read means the retired dialect stays documented.

    A dialect the transport still accepts cannot be left undocumented, so the
    four retired names must survive — but ONLY inside a marked legacy region. A
    retired term outside one is a name the migration did not finish.
    """
    text = contract_text()
    gloss = legacy_region(text)
    assert LEGACY_START in text and LEGACY_END in text, (
        "the contract must mark where a retired term is a declared gloss"
    )
    for retired in RETIRED_ROLES:
        assert retired in gloss, f"the legacy gloss must render the retired role '{retired}'"
    outside = prose_only(outside_legacy_region(text))
    for retired in RETIRED_ROLES:
        assert retired not in outside, (
            f"'{retired}' is used as a name outside the declared legacy region"
        )


def test_the_contract_declares_the_versioned_envelope():
    """§3 declares the envelope version and the one thing it must never carry."""
    section = contract_text().split("## 3.", 1)[1].split("## 4.", 1)[0]
    for marker in ("`schema`", "Absent means `1`", "schema-2 envelope"):
        assert marker in section, f"§3 must declare '{marker}'"
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["schema"]["enum"] == [1, 2], (
        "the envelope schema must carry the two versions the contract declares"
    )


def test_contract_declares_every_directive_verb():
    text = contract_text()
    for verb in DIRECTIVE_VERBS:
        assert verb in text, f"the contract must declare the directive verb '{verb}'"


def test_contract_declares_the_trust_model_verbatim():
    """Both dialects, verbatim: the current rules and the retired rendering.

    Schema 1 is still accepted on read, so the contract must declare both. The
    retired four live inside the marked legacy region — asserted here so a later
    edit cannot quietly delete the dialect the transport still understands.
    """
    text = contract_text()
    for rule in TRUST_RULES:
        assert rule in text, f"the contract must declare the trust rule '{rule}'"
    gloss = legacy_region(text)
    for rule in RETIRED_TRUST_RULES:
        assert rule in gloss, f"the legacy gloss must declare the rule '{rule}'"


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
        # The metaphor is gone (issue #777) and the INVARIANT it carried is what
        # §7.1 must still declare (asserted on the sentence as written: the
        # casing of the opening word is layout, not doctrine).
        "The dispatcher never picks work",
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
    """The new clauses are additive: the DSv4FNone order and the roles stay.

    The roles are asserted in the CURRENT vocabulary (issue #777): the standing
    directive is a normative surface, so a retired name here would be the
    half-done rename the migration exists to prevent — and the invariant the
    retired metaphor carried is asserted in its place.
    """
    body = standing_body()
    for marker in (
        "DSv4FNone",
        "DeepSeek v4.1 Flash",
        "the dispatcher session",
        "never picks work",
        "ROLES",
        "director = DSv4PM (DeepSeek v4 Pro Max)",
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
