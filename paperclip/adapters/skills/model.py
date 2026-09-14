"""Data contracts and the error taxonomy for the skills adapter (issue #419).

The vocabulary is deliberately small and closed:

* a declaration is a :class:`SkillDeclaration` — a ``SKILL.md`` with a
  :class:`Provenance` block (GR-10) and a :class:`Requirement` set;
* the MCP surface is a :class:`ToolProjection` — a *view* of the tool authority
  ``gateway/mcp/``, never a second allowlist;
* every refusal raises a :class:`SkillRefused` subclass whose message names the
  offender, so a gate can quote it instead of a bare exit code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

#: The declaration kinds a ``SKILL.md`` may carry. A *skill* is instructions an
#: agent loads; a *plugin* additionally wires an internal tool the profile must
#: already grant.
KINDS: Tuple[str, ...] = ("skill", "plugin")

#: The verdict vocabulary of a provenance record (the issue #145 harvest map).
VERDICTS: Tuple[str, ...] = ("AUTHORED", "READY", "PATTERN", "REFERENCE")

#: The three provenance columns a harvested or third-party asset must record
#: (GR-10), plus the verdict that says how it was used.
PROVENANCE_FIELDS: Tuple[str, ...] = ("repo", "path", "license", "verdict")

#: The registry/ledger path of the projection view, relative to the repo root.
PROJECTION_PATH = "paperclip/adapters/skills/mcp_tools.json"

#: The projection's declared source function — the tool authority it is a view of.
MCP_SOURCE_FUNCTION = "gateway.mcp.tools.build_registry"

#: The projection's declared source files. The gate fails if the view claims a
#: source other than the tool authority in ``gateway/mcp/``.
MCP_SOURCE_FILES: Tuple[str, ...] = ("gateway/mcp/tools.py", "gateway/mcp/model.py")


class SkillAdapterError(Exception):
    """Base class for every error this package raises."""


class CannotAssess(SkillAdapterError):
    """The input needed to reach a verdict is absent (maps to exit code 2)."""


class SkillRefused(SkillAdapterError):
    """A load or structural check refused the input, naming the offender."""


class UndeclaredSkillError(SkillRefused):
    """A ``SKILL.md`` exists on disk but is not in the declared registry."""


class MissingProvenanceError(SkillRefused):
    """A declaration carries no complete provenance record (GR-10)."""


class VendoredImplementationError(SkillRefused):
    """A declaration directory carries a copied implementation, not a reference."""


class CapabilityNotGrantedError(SkillRefused):
    """The skill requires a capability the agent profile does not grant."""


class ToolNotGrantedError(SkillRefused):
    """The plugin requires an internal tool the agent profile does not grant."""


class ToolNotProjectedError(SkillRefused):
    """The skill requires an MCP tool that is absent from the projected surface."""


@dataclass(frozen=True)
class Provenance:
    """Where a skill came from and under what terms — the GR-10 record."""

    repo: str
    path: str
    license: str
    verdict: str

    def is_complete(self) -> bool:
        return all(
            isinstance(getattr(self, name), str) and getattr(self, name).strip()
            for name in PROVENANCE_FIELDS
        ) and self.verdict in VERDICTS

    def missing(self) -> List[str]:
        """The provenance columns that are absent or outside the vocabulary."""
        gaps = [
            name
            for name in PROVENANCE_FIELDS
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip()
        ]
        if "verdict" not in gaps and self.verdict not in VERDICTS:
            gaps.append("verdict(unknown:%s)" % self.verdict)
        return gaps

    def as_dict(self) -> Dict[str, str]:
        return {
            "repo": self.repo,
            "path": self.path,
            "license": self.license,
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class Requirement:
    """What a declaration asks of the agent that loads it.

    ``capabilities`` are drawn from the profile vocabulary
    (``registry/profiles/catalog.yaml`` capabilities); ``tools`` from the profile
    tool vocabulary; ``mcp_tools`` from the projected MCP surface. Every axis is
    a *narrowing* request: the profile must already grant it.
    """

    capabilities: Tuple[str, ...] = ()
    tools: Tuple[str, ...] = ()
    mcp_tools: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, List[str]]:
        return {
            "capabilities": list(self.capabilities),
            "tools": list(self.tools),
            "mcp_tools": list(self.mcp_tools),
        }


@dataclass(frozen=True)
class SkillDeclaration:
    """A parsed, declared ``SKILL.md`` — declaration plus reference, never a copy."""

    id: str
    kind: str
    name: str
    description: str
    provenance: Provenance
    requires: Requirement
    declaration_path: str
    directory: Optional[str] = None


@dataclass(frozen=True)
class ToolProjection:
    """The projected MCP tool surface — a view of ``gateway/mcp/``."""

    schema: str
    derived_from: Tuple[str, ...]
    source_function: str
    tools: Tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> Dict[str, object]:
        return {
            "schema": self.schema,
            "derived_from": list(self.derived_from),
            "source_function": self.source_function,
            "tools": list(self.tools),
        }


@dataclass(frozen=True)
class LoadDecision:
    """The outcome of a load-gate evaluation."""

    skill_id: str
    profile_id: str
    refused: bool
    reasons: Tuple[str, ...]
    granted_capabilities: Tuple[str, ...] = ()
    used_capabilities: Tuple[str, ...] = ()

    def widened(self) -> bool:
        """True when the requested capabilities exceed what the profile grants.

        For an **allowed** load this is always False by construction — the loader
        refuses anything it cannot grant — and the loader asserts it, so the
        no-widening property is stated rather than assumed. For a **refused** load
        it is expected to be True: that excess is precisely the refusal reason.
        """
        return not set(self.used_capabilities) <= set(self.granted_capabilities)


@dataclass(frozen=True)
class ProfileAuthority:
    """The one authority for what an agent may use: its registry profile."""

    id: str
    version: str
    path: str
    capabilities: Tuple[str, ...]
    tools: Tuple[str, ...]
