"""The declared claim-resolution policy, read — never restated (issue #592).

``claim-policy.json``, beside this module, is the **control artifact** of the
module-brief surface: it states what makes a brief line resolvable, what a
non-resolving line produces, and that a target-set module with no vendor
``module.json`` renders ``target-pending`` — pending is never rendered as
shipped.

The composer and the claim vocabulary read *this* object instead of carrying
their own copy of those rules, which is the difference between a declared
control and a decoration: editing the policy changes what the composer refuses,
and the gate proves it by doctoring the file and requiring the refusal to change.

Two refusals the loader makes on the policy itself, because a policy that
disagrees with the authority is worse than no policy:

* the state it calls pending must be one of the authority's three;
* it may not declare that pending *can* render as shipped — the whole point of
  the artifact is the opposite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from governance.modules.model import CannotAssess, STATES

#: The policy file, beside this module (it travels with the package).
POLICY_FILE = "claim-policy.json"

#: The policy's own schema tag.
POLICY_SCHEMA = "ao.module-brief-claim-policy/v1"

#: Citation bases a policy may declare, mapped to the resolver that implements
#: them. A policy naming anything else is refused rather than silently ignored.
BASES: Tuple[str, ...] = ("repository-root", "hub-root")


@dataclass(frozen=True)
class ClaimPolicy:
    """The declared rules, typed, with the authority's vocabulary checked."""

    registry_prefix: str
    bases: Tuple[str, ...]
    unresolved_code: str
    artifact: str
    pending_state: str
    pending_renders: str
    pending_shipped_must_be: bool
    pending_blocker_required: bool
    pending_shipped_code: str
    pending_blocker_code: str

    def resolves_path(self, base: str, repo_root: Path, hub_root: Path) -> Path:
        """The directory a declared citation base stands for."""
        if base == "repository-root":
            return Path(repo_root)
        if base == "hub-root":
            return Path(hub_root)
        raise CannotAssess(
            "the claim policy declares the citation base {!r}, which no resolver "
            "implements (known bases: {})".format(base, list(BASES))
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "registry_prefix": self.registry_prefix,
            "bases": list(self.bases),
            "unresolved_code": self.unresolved_code,
            "artifact": self.artifact,
            "pending_state": self.pending_state,
            "pending_renders": self.pending_renders,
            "pending_shipped_must_be": self.pending_shipped_must_be,
            "pending_blocker_required": self.pending_blocker_required,
            "pending_shipped_code": self.pending_shipped_code,
            "pending_blocker_code": self.pending_blocker_code,
        }


def policy_path(directory: Optional[Path] = None) -> Path:
    base = Path(directory) if directory is not None else Path(__file__).resolve().parent
    return base / POLICY_FILE


def _mapping(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise CannotAssess("the claim policy declares {} as {!r}, expected an object".format(what, value))
    return value


def load(directory: Optional[Path] = None) -> ClaimPolicy:
    """Read the declared policy. Unreadable is CANNOT-ASSESS, never a default."""
    path = policy_path(directory)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CannotAssess("the claim policy is unreadable: {} ({})".format(path, exc))
    except ValueError as exc:
        raise CannotAssess("the claim policy is not valid JSON: {} ({})".format(path, exc))
    data = _mapping(data, "the document")
    if data.get("schema") != POLICY_SCHEMA:
        raise CannotAssess(
            "the claim policy is not the declared one: schema is {!r}, expected {!r}".format(
                data.get("schema"), POLICY_SCHEMA
            )
        )

    resolution = _mapping(data.get("resolution"), "resolution")
    unresolved = _mapping(resolution.get("unresolved"), "resolution.unresolved")
    pending = _mapping(data.get("pending"), "pending")
    refusals = _mapping(pending.get("refusals"), "pending.refusals")

    prefix = resolution.get("registry_prefix")
    if not isinstance(prefix, str) or not prefix:
        raise CannotAssess(
            "the claim policy declares registry_prefix {!r}; a citation prefix must be "
            "a non-empty string".format(prefix)
        )
    bases = tuple(str(base) for base in resolution.get("bases") or ())
    if not bases:
        raise CannotAssess("the claim policy declares no citation base to resolve against")
    for base in bases:
        if base not in BASES:
            raise CannotAssess(
                "the claim policy declares the citation base {!r}; the resolvers implement "
                "{}".format(base, list(BASES))
            )

    code = unresolved.get("code")
    artifact = unresolved.get("artifact")
    if not isinstance(code, str) or not code:
        raise CannotAssess("the claim policy declares no refusal code for an unresolved claim")
    if not isinstance(artifact, str) or not artifact:
        raise CannotAssess("the claim policy declares no artifact to name the line in")

    state = pending.get("state")
    if state not in STATES:
        raise CannotAssess(
            "the claim policy calls {!r} pending, which is not one of the authority's "
            "three states {}".format(state, list(STATES))
        )
    if pending.get("renders") != state:
        raise CannotAssess(
            "the claim policy says a pending module renders {!r} while it calls the state "
            "{!r} — the policy may not render a state other than the one it names".format(
                pending.get("renders"), state
            )
        )
    if pending.get("never_rendered_as_shipped") is not True:
        raise CannotAssess(
            "the claim policy does not declare pending as never-rendered-as-shipped; that "
            "declaration is the rule this artifact exists to hold"
        )
    if pending.get("shipped_must_be") is not False:
        raise CannotAssess(
            "the claim policy declares shipped_must_be {!r} for a pending module; pending may "
            "only be rendered as not shipped".format(pending.get("shipped_must_be"))
        )

    return ClaimPolicy(
        registry_prefix=prefix,
        bases=bases,
        unresolved_code=code,
        artifact=artifact,
        pending_state=str(state),
        pending_renders=str(pending.get("renders")),
        pending_shipped_must_be=False,
        pending_blocker_required=bool(pending.get("blocker_required")),
        pending_shipped_code=str(refusals.get("rendered_shipped") or ""),
        pending_blocker_code=str(refusals.get("no_blocker") or ""),
    )


__all__ = [
    "BASES",
    "POLICY_FILE",
    "POLICY_SCHEMA",
    "ClaimPolicy",
    "load",
    "policy_path",
]
