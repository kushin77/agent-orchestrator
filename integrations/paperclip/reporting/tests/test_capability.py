"""The capability contract: declared, granted, homed, resolvable (issue #447)."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.paperclip.reporting import capability
from integrations.paperclip.reporting.model import CannotAssess

HUB = "vendor/CMR"


def _codes(findings) -> set:
    return {finding.code for finding in findings}


def _subjects(findings, code: str) -> set:
    return {finding.subject for finding in findings if finding.code == code}


def test_the_shipped_persona_satisfies_the_contract(repo_root: Path):
    assert capability.check(repo_root, repo_root / HUB) == ()


def test_the_capability_is_declared_with_the_tools_it_needs(repo_root: Path):
    card = capability.load_card(repo_root)
    assert capability.CAPABILITY_ID in card["capabilitySet"]
    for tool in capability.REQUIRED_TOOLS:
        assert tool in card["toolAllowlist"]
    assert capability.ARTIFACT_HOME in card["memoryScope"]
    # The read-only property the issue pins: the brief reads three sources and
    # writes only its own artifact — it never gains a board or shell tool.
    assert set(capability.REQUIRED_TOOLS) == {"file_read", "file_write"}
    assert "gh_issue" not in card["toolAllowlist"]
    assert "shell_exec" not in card["toolAllowlist"]


def test_a_card_that_does_not_declare_the_capability_is_refused_by_name(tree: Path):
    card = tree / capability.CARD
    text = card.read_text(encoding="utf-8")
    doctored = text.replace("  - memory-ops\n  - module-brief\n", "  - memory-ops\n", 1)
    assert doctored != text, "the mutation did not land"
    card.write_text(doctored, encoding="utf-8")
    findings = capability.check(tree, tree / HUB)
    assert "BRIEF-CAPABILITY-UNDECLARED" in _codes(findings)
    assert _subjects(findings, "BRIEF-CAPABILITY-UNDECLARED") == {"module-brief"}


def test_a_capability_whose_tool_is_not_granted_is_refused_by_name(tree: Path):
    card = tree / capability.CARD
    text = card.read_text(encoding="utf-8")
    doctored = text.replace("  - file_read\n", "", 1)
    assert doctored != text, "the mutation did not land"
    card.write_text(doctored, encoding="utf-8")
    findings = capability.check(tree, tree / HUB)
    assert _subjects(findings, "BRIEF-CAPABILITY-TOOL-UNGRANTED") == {"file_read"}


def test_a_missing_artifact_home_is_refused_by_name(tree: Path):
    card = tree / capability.CARD
    text = card.read_text(encoding="utf-8")
    card.write_text(
        text.replace("memoryScope:\n  - user\n  - repository\n", "memoryScope:\n  - user\n"),
        encoding="utf-8",
    )
    findings = capability.check(tree, tree / HUB)
    assert _subjects(findings, "BRIEF-ARTIFACT-HOME-UNGRANTED") == {"repository"}


def test_a_seed_that_did_not_follow_the_card_is_refused_by_name(tree: Path):
    seed = tree / capability.SEED
    text = seed.read_text(encoding="utf-8")
    doctored = text.replace("capabilitySet:\n  - research\n  - docs-authoring\n  - memory-ops\n  - module-brief\n", "capabilitySet:\n  - research\n  - docs-authoring\n  - memory-ops\n", 1)
    assert doctored != text, "the mutation did not land"
    seed.write_text(doctored, encoding="utf-8")
    findings = capability.check(tree, tree / HUB)
    assert "BRIEF-SEED-DRIFT" in _codes(findings)


def test_a_source_that_does_not_resolve_is_refused_by_name(tree: Path):
    (tree / capability.SOURCES[1].path).unlink()
    findings = capability.check(tree, tree / HUB)
    assert _subjects(findings, "BRIEF-SOURCE-MISSING") == {"board snapshot"}


def test_an_unreadable_card_is_cannot_assess_never_a_pass(tree: Path):
    (tree / capability.CARD).unlink()
    with pytest.raises(CannotAssess):
        capability.check(tree, tree / HUB)


def test_the_contract_carries_the_three_read_only_sources():
    names = [source.name for source in capability.SOURCES]
    assert names == ["module registry", "board snapshot", "hub catalog"]
    contract = capability.as_dict()
    assert contract["capability"] == "module-brief"
    assert contract["required_tools"] == list(capability.REQUIRED_TOOLS)
    assert contract["artifact_home"] == "repository"
