"""The load gate: extensibility narrows authority, it never widens it."""

from __future__ import annotations

from pathlib import Path

import pytest

from paperclip.adapters.skills import loader
from paperclip.adapters.skills.model import CannotAssess

PKG = Path("paperclip/adapters/skills")


def reasons(root: Path, skill: str, profile: str) -> list:
    return list(loader.check_load(root, skill, profile).reasons)


def test_granted_loads_are_allowed(repo_root: Path) -> None:
    allowed = [
        ("ticket-contract-read", "paperclip"),
        ("mcp-tool-projection", "paperclip"),
        ("sme-card-authoring", "paperclip"),
        ("board-sync-plugin", "orchestrator"),
        ("claim-gate-check", "coder"),
    ]
    for skill, profile in allowed:
        decision = loader.check_load(repo_root, skill, profile)
        assert not decision.refused, (skill, profile, decision.reasons)
        assert not decision.widened()
        assert set(loader.effective_capabilities(decision)) <= set(decision.granted_capabilities)


def test_capability_not_granted_is_refused_by_name(repo_root: Path) -> None:
    found = reasons(repo_root, "mcp-tool-projection", "coder")
    assert any("'research'" in reason and "'coder'" in reason for reason in found)


def test_plugin_tool_not_granted_is_refused_by_name(repo_root: Path) -> None:
    found = reasons(repo_root, "claim-gate-check", "paperclip")
    assert any("'shell_exec'" in reason and "'paperclip'" in reason for reason in found)


def test_undeclared_skill_is_refused_by_name(repo_root: Path) -> None:
    found = reasons(repo_root, "no-such-skill", "paperclip")
    assert any("no-such-skill" in reason and "undeclared" in reason.lower() for reason in found)


def test_mcp_tool_absent_from_projection_is_refused(scratch_root: Path) -> None:
    path = scratch_root / PKG / "library" / "ticket-contract-read" / "SKILL.md"
    text = path.read_text(encoding="utf-8").replace("    - kb.query", "    - kb.not-projected", 1)
    path.write_text(text, encoding="utf-8")
    found = reasons(scratch_root, "ticket-contract-read", "paperclip")
    assert any("'kb.not-projected'" in reason for reason in found)


def test_missing_provenance_is_refused(scratch_root: Path) -> None:
    path = scratch_root / PKG / "library" / "ticket-contract-read" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    out, skipping = [], False
    for line in text.split("\n"):
        if line.startswith("provenance:"):
            skipping = True
            continue
        if skipping and (line.startswith("  ") or not line.strip()):
            continue
        skipping = False
        out.append(line)
    path.write_text("\n".join(out), encoding="utf-8")
    found = reasons(scratch_root, "ticket-contract-read", "paperclip")
    assert any("provenance" in reason for reason in found)


def test_vendored_implementation_is_refused(scratch_root: Path) -> None:
    directory = scratch_root / PKG / "library" / "ticket-contract-read"
    (directory / "copied_upstream.py").write_text("VALUE = 1\n", encoding="utf-8")
    found = reasons(scratch_root, "ticket-contract-read", "paperclip")
    assert any("copied_upstream.py" in reason for reason in found)


def test_unknown_profile_is_cannot_assess(repo_root: Path) -> None:
    with pytest.raises(CannotAssess):
        loader.check_load(repo_root, "ticket-contract-read", "no-such-profile")


def test_refused_load_confers_nothing(repo_root: Path) -> None:
    decision = loader.check_load(repo_root, "claim-gate-check", "paperclip")
    assert decision.refused
    assert loader.effective_capabilities(decision) == ()
    assert decision.widened()
