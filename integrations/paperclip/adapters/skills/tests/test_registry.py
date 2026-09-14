"""The declared registry is closed: an undeclared declaration is refused."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.paperclip.adapters.skills import registry
from integrations.paperclip.adapters.skills.model import (
    MissingProvenanceError,
    SkillRefused,
    UndeclaredSkillError,
)

PKG = Path("integrations/paperclip/adapters/skills")

ROGUE = """---
id: rogue-skill
kind: skill
name: Rogue skill
description: On disk, absent from the registry.
provenance:
  repo: example/rogue
  path: skills/rogue/SKILL.md
  license: MIT
  verdict: REFERENCE
---
# Rogue skill
"""


def strip_provenance(text: str) -> str:
    """Drop the ``provenance:`` block (its key line and indented children)."""
    out, skipping = [], False
    for line in text.split("\n"):
        if line.startswith("provenance:"):
            skipping = True
            continue
        if skipping and (line.startswith("  ") or not line.strip()):
            continue
        skipping = False
        out.append(line)
    return "\n".join(out)


def test_declared_and_discovered_agree(repo_root: Path) -> None:
    declared = {entry["declaration"] for entry in registry.declared_entries(repo_root).values()}
    assert declared == set(registry.discover_declarations(repo_root))


def test_registry_findings_clean(repo_root: Path) -> None:
    assert registry.registry_findings(repo_root) == []


def test_every_declaration_loads_with_provenance(repo_root: Path) -> None:
    declarations = registry.load_declarations(repo_root)
    assert declarations
    for declaration in declarations.values():
        assert declaration.provenance.is_complete()
        assert declaration.provenance.verdict in ("AUTHORED", "READY", "PATTERN", "REFERENCE")


def test_undeclared_skill_is_refused_by_name(scratch_root: Path) -> None:
    rogue_dir = scratch_root / PKG / "library" / "rogue-skill"
    rogue_dir.mkdir(parents=True)
    (rogue_dir / "SKILL.md").write_text(ROGUE, encoding="utf-8")

    findings = registry.registry_findings(scratch_root)
    assert any("undeclared" in finding and "rogue-skill" in finding for finding in findings)

    with pytest.raises(UndeclaredSkillError):
        registry.resolve(scratch_root, "rogue-skill")


def test_declared_but_missing_declaration_fails(scratch_root: Path) -> None:
    target = scratch_root / PKG / "library" / "ticket-contract-read" / "SKILL.md"
    target.unlink()
    findings = registry.registry_findings(scratch_root)
    assert any("ticket-contract-read" in finding for finding in findings)


def test_unknown_kind_is_refused(scratch_root: Path) -> None:
    target = scratch_root / PKG / "library" / "ticket-contract-read" / "SKILL.md"
    text = target.read_text(encoding="utf-8").replace("kind: skill", "kind: daemon", 1)
    target.write_text(text, encoding="utf-8")
    with pytest.raises(SkillRefused):
        registry.resolve(scratch_root, "ticket-contract-read")


def test_missing_provenance_is_refused(scratch_root: Path) -> None:
    target = scratch_root / PKG / "library" / "ticket-contract-read" / "SKILL.md"
    target.write_text(strip_provenance(target.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises((MissingProvenanceError, SkillRefused)):
        registry.resolve(scratch_root, "ticket-contract-read")


def test_duplicate_registry_id_is_refused(scratch_root: Path) -> None:
    path = scratch_root / PKG / "registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["declarations"].append(dict(data["declarations"][0]))
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SkillRefused):
        registry.declared_entries(scratch_root)
