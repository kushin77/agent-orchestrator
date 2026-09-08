"""Closed-vocabulary and AgentProfile seed access (consumes the #9 contract).

The Agent Identity + Registry service *consumes* the issue-#9 contract; it
never redefines field names or vocabulary. This module reads the two artifacts
the #9 lane owns:

- ``registry/profiles/catalog.yaml`` - the closed platform vocabulary (tool ids,
  capability ids, tiers);
- ``registry/profiles/seeds/<id>.<version>.yaml`` - immutable AgentProfile
  seeds (``coder``, ``reviewer``, ``orchestrator``, ``researcher``,
  ``data-agent``).

A reference to an unknown capability, tool or profile is rejected (fail
closed). Resolution is read-only: nothing here writes to ``registry/profiles``.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple

import yaml

from .errors import (
    ProfileResolutionError,
    UnknownCapabilityError,
    UnknownProfileError,
    UnknownToolError,
    VocabularyUnavailableError,
)

_PROFILES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "profiles"
)
_CATALOG_PATH = os.path.join(_PROFILES_DIR, "catalog.yaml")
_SEEDS_DIR = os.path.join(_PROFILES_DIR, "seeds")


@dataclass(frozen=True)
class ClosedCatalog:
    """The closed platform vocabulary loaded from ``profiles/catalog.yaml``."""

    capabilities: FrozenSet[str]
    tools: FrozenSet[str]
    tiers: FrozenSet[str]


@dataclass(frozen=True)
class ResolvedProfile:
    """A frozen AgentProfile seed, reduced to the fields the service consumes."""

    id: str
    version: str
    owner: str
    capability_set: Tuple[str, ...]
    tool_allowlist: Tuple[str, ...]
    model_tier: str


_CATALOG_CACHE: Optional[ClosedCatalog] = None


def load_catalog() -> ClosedCatalog:
    """Load (and cache) the closed platform vocabulary.

    Raises ``VocabularyUnavailableError`` if catalog.yaml is missing or cannot
    be parsed - the service cannot validate references against nothing.
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE
    if not os.path.isfile(_CATALOG_PATH):
        raise VocabularyUnavailableError(
            f"closed vocabulary catalog not found at {_CATALOG_PATH} "
            "(registry/profiles/catalog.yaml must be present)"
        )
    try:
        with open(_CATALOG_PATH, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise VocabularyUnavailableError(
            f"could not parse closed vocabulary catalog {_CATALOG_PATH}: {exc}"
        ) from exc
    capabilities = frozenset((data.get("capabilities") or {}).keys())
    tools = frozenset((data.get("tools") or {}).keys())
    tiers = frozenset((data.get("tiers") or {}).keys())
    if not capabilities or not tools:
        raise VocabularyUnavailableError(
            f"closed vocabulary catalog {_CATALOG_PATH} is empty"
        )
    _CATALOG_CACHE = ClosedCatalog(capabilities=capabilities, tools=tools, tiers=tiers)
    return _CATALOG_CACHE


def require_capability(capability: str, catalog: Optional[ClosedCatalog] = None) -> None:
    """Fail closed unless the capability id is in the closed catalog."""
    catalog = catalog or load_catalog()
    if capability not in catalog.capabilities:
        raise UnknownCapabilityError(
            f"unknown capability {capability!r} (not in the closed platform catalog)"
        )


def require_tool(tool: str, catalog: Optional[ClosedCatalog] = None) -> None:
    """Fail closed unless the tool id is in the closed catalog."""
    catalog = catalog or load_catalog()
    if tool not in catalog.tools:
        raise UnknownToolError(
            f"unknown tool {tool!r} (not in the closed platform catalog)"
        )


def resolve_profile(profile_ref: str) -> ResolvedProfile:
    """Resolve ``profile_ref`` (``<id>`` or ``<id>@<version>``) to a frozen seed.

    A bare ``<id>`` resolves only when exactly one immutable seed version exists
    for it; when several versions exist the caller must pin ``<id>@<version>``
    (fail closed rather than guess). Capabilities and tools of the resolved
    profile are validated against the closed catalog.
    """
    catalog = load_catalog()
    if not isinstance(profile_ref, str) or not profile_ref:
        raise UnknownProfileError(f"empty profile reference {profile_ref!r}")

    if "@" in profile_ref:
        profile_id, _, version = profile_ref.partition("@")
        candidates = [os.path.join(_SEEDS_DIR, f"{profile_id}.{version}.yaml")]
    else:
        profile_id, version = profile_ref, None
        candidates = sorted(glob.glob(os.path.join(_SEEDS_DIR, f"{profile_id}.*.yaml")))

    existing = [candidate for candidate in candidates if os.path.isfile(candidate)]
    if not existing:
        raise UnknownProfileError(
            f"no AgentProfile seed for reference {profile_ref!r} "
            f"(looked under {_SEEDS_DIR})"
        )
    if len(existing) > 1:
        raise ProfileResolutionError(
            f"profile {profile_ref!r} has multiple published versions "
            f"({[os.path.basename(p) for p in existing]}); pin <id>@<version>"
        )

    path = existing[0]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ProfileResolutionError(
            f"could not parse profile seed {path}: {exc}"
        ) from exc

    capability_set = tuple(data.get("capabilitySet") or ())
    tool_allowlist = tuple(data.get("toolAllowlist") or ())
    for capability in capability_set:
        require_capability(capability, catalog)
    for tool in tool_allowlist:
        require_tool(tool, catalog)
    if not capability_set or not tool_allowlist:
        raise ProfileResolutionError(f"profile seed {path} declares empty sets")

    return ResolvedProfile(
        id=data.get("id", profile_id),
        version=data.get("version", version or ""),
        owner=data.get("owner", ""),
        capability_set=capability_set,
        tool_allowlist=tool_allowlist,
        model_tier=data.get("defaultModelTier", "LOW"),
    )
