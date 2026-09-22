"""The load gate: extensibility may never widen an agent's authority (issue #419).

An agent profile (``registry/profiles/seeds/<id>.<ver>.yaml``) is the authority
for what an agent may use — its ``capabilitySet`` and ``toolAllowlist``. A skill
or plugin may only **narrow** that authority. The gate therefore refuses, by
name:

* a skill that is not in the declared registry (undeclared);
* a declaration with no complete provenance record (GR-10);
* a declaration directory carrying anything but ``SKILL.md`` (vendored code);
* a requirement for a capability the profile does not grant;
* a plugin requirement for an internal tool the profile does not grant;
* a requirement for an MCP tool that is absent from the projected surface.

The refusal is the point: without it, loading a skill would be a privilege
escalation. The decision object records the granted and requested capability sets
so the no-widening property is stated, not assumed.

---knowledge---
module_id: integrations.paperclip.adapters.skills.loader
system: integrations
app: paperclip
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: [profile_seed_path, load_profile, check_load, effective_capabilities, load_findings]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from .model import (
    CannotAssess,
    LoadDecision,
    ProfileAuthority,
    SkillDeclaration,
    SkillRefused,
)
from .projection import load_projection
from .registry import resolve

#: The seed directory the profile authority is read from (read-only input).
PROFILE_SEED_DIR = "registry/profiles/seeds"


def _yaml_load(path: Path) -> object:
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - guarded, never vacuous
        raise CannotAssess("skills: PyYAML is not importable (%s)" % exc) from exc
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CannotAssess("skills: %s is not valid YAML: %s" % (path, exc)) from exc


def profile_seed_path(root: Path, profile_id: str) -> Path:
    """The seed file for a profile id (newest SemVer first, fail closed)."""
    seeds = sorted((root / PROFILE_SEED_DIR).glob("%s.*.yaml" % profile_id))
    if not seeds:
        raise CannotAssess(
            "skills: no profile seed for %r under %s (the profile is the authority; "
            "an unknown profile cannot be assessed)" % (profile_id, PROFILE_SEED_DIR)
        )
    return seeds[-1]


def load_profile(root: Path, profile_id: str) -> ProfileAuthority:
    """Read the one authority for what an agent may use."""
    path = profile_seed_path(root, profile_id)
    data = _yaml_load(path)
    if not isinstance(data, dict):
        raise CannotAssess("skills: profile %s is not a mapping" % path)
    for key in ("id", "version", "capabilitySet", "toolAllowlist"):
        if key not in data:
            raise CannotAssess("skills: profile %s has no %r" % (path, key))
    if str(data["id"]) != profile_id:
        raise CannotAssess(
            "skills: profile %s declares id %r, not %r" % (path, data["id"], profile_id)
        )
    return ProfileAuthority(
        id=str(data["id"]),
        version=str(data["version"]),
        path=str(path),
        capabilities=tuple(str(c) for c in data["capabilitySet"]),
        tools=tuple(str(t) for t in data["toolAllowlist"]),
    )


def _refusal_reasons(
    declaration: SkillDeclaration,
    profile: ProfileAuthority,
    projected: Tuple[str, ...],
) -> List[str]:
    reasons: List[str] = []
    granted = set(profile.capabilities)
    granted_tools = set(profile.tools)
    projected_tools = set(projected)

    for capability in declaration.requires.capabilities:
        if capability not in granted:
            reasons.append(
                "skill %r requires capability %r not granted by profile %r "
                "(extensibility must not widen the agent's authority)"
                % (declaration.id, capability, profile.id)
            )
    for tool in declaration.requires.tools:
        if tool not in granted_tools:
            reasons.append(
                "plugin %r requires tool %r not granted by profile %r"
                % (declaration.id, tool, profile.id)
            )
    for mcp_tool in declaration.requires.mcp_tools:
        if mcp_tool not in projected_tools:
            reasons.append(
                "skill %r requires MCP tool %r which is absent from the projected "
                "surface (the projection is derived from gateway/mcp/)" % (declaration.id, mcp_tool)
            )
    return reasons


def check_load(root: Path, skill_id: str, profile_id: str) -> LoadDecision:
    """Evaluate loading ``skill_id`` as ``profile_id``; refusals are returned.

    A structural refusal (undeclared skill, missing provenance, vendored code) is
    caught here and turned into a named refusal reason, so the gate always sees a
    verdict rather than a traceback.
    """
    try:
        declaration = resolve(root, skill_id)
    except SkillRefused as exc:
        return LoadDecision(
            skill_id=skill_id, profile_id=profile_id, refused=True, reasons=(str(exc),)
        )

    profile = load_profile(root, profile_id)
    projection = load_projection(root)
    reasons = _refusal_reasons(declaration, profile, projection.tools)
    decision = LoadDecision(
        skill_id=skill_id,
        profile_id=profile_id,
        refused=bool(reasons),
        reasons=tuple(reasons),
        granted_capabilities=profile.capabilities,
        used_capabilities=declaration.requires.capabilities,
    )
    if not decision.refused and decision.widened():
        # An allowed load is exactly one whose every requirement is already
        # granted; this can only fail if the refusal loop above is wrong.
        raise CannotAssess(  # pragma: no cover - defensive invariant
            "skills: load of %r as %r would widen authority" % (skill_id, profile_id)
        )
    return decision


def effective_capabilities(decision: LoadDecision) -> Tuple[str, ...]:
    """The capabilities an allowed load actually confers — granted minus unused.

    The result is a subset of the profile's ``capabilitySet`` by construction: a
    load can only narrow what the profile already grants.
    """
    if decision.refused:
        return ()
    granted = set(decision.granted_capabilities)
    used = set(decision.used_capabilities)
    return tuple(sorted(used & granted))


def load_findings(root: Path, skill_id: str, profile_id: str) -> List[str]:
    """The refusal reasons for a load, or an empty list when it is allowed."""
    return list(check_load(root, skill_id, profile_id).reasons)
