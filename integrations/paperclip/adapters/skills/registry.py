"""The declared ``SKILL.md`` registry (issue #419).

The registry is a **closed set**: ``registry.json`` names every declaration the
platform may load. Discovery walks the tree for ``SKILL.md`` files and refuses
any that the registry does not declare — an undeclared skill is *refused*, never
silently loaded (the allowlist-not-denylist doctrine the tool registry uses).

The registry mirrors the agent-registry discipline (``registry/profiles/``,
``registry/personas/``): one declaration, one owner, one gate. It stores no
behaviour — the declaration lives in the ``SKILL.md`` itself, and the registry
only says which declarations are loadable.

---knowledge---
module_id: integrations.paperclip.adapters.skills.registry
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [package_dir, registry_path, load_registry, declared_entries, discover_declarations, load_declarations, registry_findings, resolve]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

from .frontmatter import DECLARATION_FILENAME, load_declaration, relative
from .model import (
    CannotAssess,
    SkillDeclaration,
    SkillRefused,
    UndeclaredSkillError,
)

#: The registry document's schema id.
REGISTRY_SCHEMA = "paperclip-skills-registry/v1"

#: The registry document, relative to the repo root.
REGISTRY_PATH = "integrations/paperclip/adapters/skills/registry.json"

#: The directories the registry governs, relative to the package directory.
DISCOVERY_DIRS: Tuple[str, ...] = ("library", "plugins")


def package_dir(root: Path) -> Path:
    return root / "integrations" / "paperclip" / "adapters" / "skills"


def registry_path(root: Path) -> Path:
    return root / REGISTRY_PATH


def load_registry(root: Path) -> Dict[str, object]:
    """Read the registry document (fail closed on a malformed registry)."""
    path = registry_path(root)
    if not path.is_file():
        raise CannotAssess("skills: no registry at %s" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CannotAssess("skills: %s is not valid JSON: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise CannotAssess("skills: %s must be a JSON object" % path)
    if data.get("schema") != REGISTRY_SCHEMA:
        raise CannotAssess(
            "skills: %s declares schema %r, expected %r"
            % (path, data.get("schema"), REGISTRY_SCHEMA)
        )
    declarations = data.get("declarations")
    if not isinstance(declarations, list) or not declarations:
        raise CannotAssess("skills: %s declares no declarations" % path)
    for index, entry in enumerate(declarations):
        if not isinstance(entry, dict):
            raise CannotAssess("skills: %s declaration #%d is not an object" % (path, index))
        for key in ("id", "kind", "declaration"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise CannotAssess(
                    "skills: %s declaration #%d is missing %r" % (path, index, key)
                )
    return data


def declared_entries(root: Path) -> Dict[str, Dict[str, str]]:
    """Declared ``id -> {kind, declaration (repo-relative)}``."""
    data = load_registry(root)
    package = package_dir(root)
    entries: Dict[str, Dict[str, str]] = {}
    for entry in data["declarations"]:  # type: ignore[index]
        skill_id = str(entry["id"])
        if skill_id in entries:
            raise SkillRefused("skills: registry declares id %r twice" % skill_id)
        declaration = package / str(entry["declaration"])
        entries[skill_id] = {
            "kind": str(entry["kind"]),
            "declaration": relative(declaration, root),
            "absolute": str(declaration),
        }
    return entries


def discover_declarations(root: Path) -> List[str]:
    """Every ``SKILL.md`` on disk under the governed directories, repo-relative."""
    package = package_dir(root)
    found: List[str] = []
    for directory in DISCOVERY_DIRS:
        base = package / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob(DECLARATION_FILENAME)):
            found.append(relative(path, root))
    return found


def load_declarations(root: Path) -> Dict[str, SkillDeclaration]:
    """Load every declared declaration, keyed by id (fail closed)."""
    declarations: Dict[str, SkillDeclaration] = {}
    for skill_id, entry in declared_entries(root).items():
        path = Path(entry["absolute"])
        declaration = load_declaration(path)
        if declaration.id != skill_id:
            raise SkillRefused(
                "skills: %s declares id %r but the registry names it %r"
                % (declaration.declaration_path, declaration.id, skill_id)
            )
        if declaration.kind != entry["kind"]:
            raise SkillRefused(
                "skills: %s declares kind %r but the registry names it %r"
                % (declaration.declaration_path, declaration.kind, entry["kind"])
            )
        declarations[skill_id] = declaration
    return declarations


def registry_findings(root: Path) -> List[str]:
    """Every way the tree disagrees with the declared registry, named."""
    findings: List[str] = []
    entries = declared_entries(root)
    declared_paths = {entry["declaration"] for entry in entries.values()}

    for skill_id, entry in sorted(entries.items()):
        if not Path(entry["absolute"]).is_file():
            findings.append(
                "declared skill %r has no declaration at %s (registry declares a "
                "file that is not there)" % (skill_id, entry["declaration"])
            )

    discovered = set(discover_declarations(root))
    for path in sorted(discovered - declared_paths):
        findings.append(
            "undeclared skill %s — a SKILL.md that is not in %s is REFUSED rather "
            "than silently loaded" % (path, REGISTRY_PATH)
        )
    for path in sorted(declared_paths - discovered):
        if Path(root / path).is_file():
            continue
        findings.append("declared declaration %s is outside the governed %s dirs"
                        % (path, list(DISCOVERY_DIRS)))

    for skill_id in sorted(entries):
        if Path(entries[skill_id]["absolute"]).is_file():
            try:
                load_declaration(Path(entries[skill_id]["absolute"]))
            except SkillRefused as exc:
                findings.append("declared skill %r is not loadable: %s" % (skill_id, exc))
    return findings


def resolve(root: Path, skill_id: str) -> SkillDeclaration:
    """Resolve one declared skill, refusing an undeclared id by name."""
    entries = declared_entries(root)
    entry = entries.get(skill_id)
    if entry is None:
        raise UndeclaredSkillError(
            "skills: REFUSED — skill %r is not in the declared registry %s "
            "(undeclared skills are never loaded)" % (skill_id, REGISTRY_PATH)
        )
    return load_declaration(Path(entry["absolute"]))
