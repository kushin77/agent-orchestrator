"""The ``SKILL.md`` declaration loader (issue #419).

A skill is a **declaration plus a reference**, never a copied implementation
(GR-10). That is enforced mechanically here:

* a declaration is a ``SKILL.md`` file with a YAML front-matter block delimited by
  ``---`` fences — the fleet's own skill-file convention;
* the declaration **directory may contain ``SKILL.md`` and nothing else**; any
  other file is a vendored implementation and is refused by name.

Loading is fail-closed: a missing front-matter block, a missing field, a kind
outside the closed vocabulary or an unreadable file all raise a
:class:`~.model.SkillRefused`/:class:`~.model.CannotAssess` naming the offender —
never a silent default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Tuple

from .model import (
    KINDS,
    PROVENANCE_FIELDS,
    VERDICTS,
    CannotAssess,
    MissingProvenanceError,
    Provenance,
    Requirement,
    SkillDeclaration,
    SkillRefused,
    VendoredImplementationError,
)

#: The one file a declaration directory may hold.
DECLARATION_FILENAME = "SKILL.md"

#: Required top-level front-matter keys.
REQUIRED_KEYS: Tuple[str, ...] = ("id", "kind", "name", "description", "provenance")


def split_front_matter(text: str, path: str) -> str:
    """Return the front-matter block of a ``SKILL.md`` document.

    The block is delimited by a leading ``---`` line and the next ``---`` line.
    A document with no such block is refused by name.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise SkillRefused(
            "skills: %s: no YAML front-matter (a declaration must open with '---')" % path
        )
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index])
    raise SkillRefused("skills: %s: front-matter is not closed by a '---' line" % path)


def parse_provenance(raw: object, path: str) -> Provenance:
    """Build a :class:`Provenance` from the front-matter block, refusing gaps."""
    if not isinstance(raw, dict):
        raise MissingProvenanceError(
            "skills: %s: declares no provenance (GR-10: repo/path/license required)" % path
        )
    fields = {}
    for name in PROVENANCE_FIELDS:
        value = raw.get(name)
        fields[name] = value if isinstance(value, str) else ""
    provenance = Provenance(**fields)
    gaps = provenance.missing()
    if gaps:
        raise MissingProvenanceError(
            "skills: %s: provenance is incomplete (missing %s)" % (path, ", ".join(gaps))
        )
    return provenance


def parse_requirements(raw: object) -> Requirement:
    """Build a :class:`Requirement` set from the optional ``requires`` block."""
    if raw is None:
        return Requirement()
    if not isinstance(raw, dict):
        raise SkillRefused("skills: 'requires' must be a mapping when present")
    unknown = sorted(set(raw) - {"capabilities", "tools", "mcp_tools"})
    if unknown:
        raise SkillRefused("skills: 'requires' has unknown key(s): %s" % ", ".join(unknown))

    def as_tuple(key: str) -> Tuple[str, ...]:
        value = raw.get(key) or []
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise SkillRefused("skills: 'requires.%s' must be a list of non-empty strings" % key)
        if len(set(value)) != len(value):
            raise SkillRefused("skills: 'requires.%s' has a duplicate entry" % key)
        return tuple(value)

    return Requirement(
        capabilities=as_tuple("capabilities"),
        tools=as_tuple("tools"),
        mcp_tools=as_tuple("mcp_tools"),
    )


def declaration_dir_files(directory: Path) -> List[str]:
    """Every entry in a declaration directory except ``SKILL.md`` itself."""
    if not directory.is_dir():
        return []
    return sorted(
        entry for entry in (p.name for p in directory.iterdir()) if entry != DECLARATION_FILENAME
    )


def assert_no_vendored_code(declaration_path: Path) -> None:
    """Refuse a declaration directory that carries anything but ``SKILL.md``."""
    extras = declaration_dir_files(declaration_path.parent)
    if extras:
        raise VendoredImplementationError(
            "skills: %s vendors non-declaration file(s) %s — a skill is a "
            "declaration plus a reference, never a copied implementation"
            % (declaration_path.parent, ", ".join(extras))
        )


def load_declaration(declaration_path: Path, enforce_no_vendoring: bool = True) -> SkillDeclaration:
    """Parse one ``SKILL.md`` into a :class:`SkillDeclaration` (fail closed)."""
    if not declaration_path.is_file():
        raise CannotAssess("skills: no declaration at %s" % declaration_path)
    if enforce_no_vendoring:
        assert_no_vendored_code(declaration_path)
    text = declaration_path.read_text(encoding="utf-8")
    block = split_front_matter(text, str(declaration_path))
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - guarded, never vacuous
        raise CannotAssess("skills: PyYAML is not importable (%s)" % exc) from exc
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise SkillRefused("skills: %s: invalid front-matter YAML: %s" % (declaration_path, exc)) from exc
    if not isinstance(data, dict):
        raise SkillRefused("skills: %s: front-matter is not a mapping" % declaration_path)

    gaps = [key for key in REQUIRED_KEYS if not data.get(key)]
    if gaps:
        raise SkillRefused(
            "skills: %s: front-matter is missing required key(s): %s"
            % (declaration_path, ", ".join(gaps))
        )
    kind = str(data["kind"])
    if kind not in KINDS:
        raise SkillRefused(
            "skills: %s: kind %r is outside the closed vocabulary %s"
            % (declaration_path, kind, list(KINDS))
        )
    provenance = parse_provenance(data.get("provenance"), str(declaration_path))
    if provenance.verdict not in VERDICTS:  # pragma: no cover - parse_provenance guards
        raise MissingProvenanceError("skills: %s: unknown verdict" % declaration_path)
    return SkillDeclaration(
        id=str(data["id"]),
        kind=kind,
        name=str(data["name"]),
        description=str(data["description"]),
        provenance=provenance,
        requires=parse_requirements(data.get("requires")),
        declaration_path=str(declaration_path),
        directory=str(declaration_path.parent),
    )


def relative(path: Path, root: Path) -> str:
    """A POSIX-style path relative to ``root`` (stable across platforms)."""
    try:
        return os.path.relpath(str(path), str(root)).replace(os.sep, "/")
    except ValueError:  # pragma: no cover - different drives (never on Linux)
        return str(path)
