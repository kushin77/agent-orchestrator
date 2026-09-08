#!/usr/bin/env python3
"""Persona -> AgentProfile mapping + SME reviewer assignment doctrine.

This module is the bridge between the persona library (issue #11) and the
AgentProfile contract (issue #9). It

1. **Materializes an AgentProfile from a persona card** - selecting the
   profile's ten contract fields from the card (``systemPromptRef``,
   ``toolAllowlist``, ``capabilitySet``, ``constraintSet``,
   ``defaultModelTier``, ``memoryScope``, ``guardrailPolicyRef``) and deriving
   ``owner`` from the persona's tenant scope. Every materialized profile is
   validated against the frozen AgentProfile schema and the live platform
   catalog (fail closed).
2. **Enforces the SME reviewer doctrine** - every PR/verdict gets an assigned
   reviewer persona, and separation of duties is enforced mechanically:

   - **Posture classes are rigid.** An ``executor`` executes primary work; a
     ``reviewer`` independently reviews an executor's PR/verdict; an
     ``auditor`` adversarially audits claims/evidence. ``guard_dispatch``
     refuses to dispatch a persona outside its posture class.
   - **An auditor persona can never be the executing persona of the task it
     audits.** Dispatching an auditor-posture persona as ``executor`` is
     rejected (``AuditorCannotExecuteError``), and an auditor is always
     distinct from the executor it audits (``SelfAuditError``).
   - **A reviewer is never the executor of the work it reviews**
     (``SelfReviewError``).

Usage (from the repo root):

    python3 registry/personas/mapping.py demo
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import jsonschema  # type: ignore
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    sys.exit(f"personas: missing dependency ({exc}); need jsonschema + PyYAML")

PKG_DIR = Path(__file__).resolve().parent
REGISTRY_DIR = PKG_DIR.parent
PROFILE_SCHEMA_PATH = REGISTRY_DIR / "profiles" / "agent-profile.schema.json"
CATALOG_PATH = REGISTRY_DIR / "profiles" / "catalog.yaml"

_PLATFORM_TENANT = "platform"

# Canonical platform reviewer personas (platform-default ids) used as the
# deterministic fallback when no domain match exists.
_DEFAULT_REVIEWER_ID = "reviewer"
_REVIEWER_POSTURES = ("reviewer",)


class PersonaMappingError(Exception):
    """Base error for the persona mapping / review-assignment module."""


class ProfileMaterializationError(PersonaMappingError):
    """Raised when a persona card cannot be materialized into a valid AgentProfile."""


class NoReviewerAvailableError(PersonaMappingError):
    """Raised when no reviewer persona (distinct from the executor) is available."""


class NoAuditorAvailableError(PersonaMappingError):
    """Raised when no auditor persona (distinct from the executor) is available."""


class SeparationViolationError(PersonaMappingError):
    """Raised on a separation-of-duties violation."""


class SelfReviewError(SeparationViolationError):
    """Raised when the reviewer of a PR/verdict would be its executor."""


class SelfAuditError(SeparationViolationError):
    """Raised when the auditor of a task would be its executor."""


class AuditorCannotExecuteError(SeparationViolationError):
    """Raised when an auditor-posture persona is dispatched as the executor."""


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_profile_schema() -> Dict[str, Any]:
    if not PROFILE_SCHEMA_PATH.exists():
        raise PersonaMappingError(
            f"AgentProfile schema not found at {PROFILE_SCHEMA_PATH} "
            "(issue #9 contract; consumed read-only)"
        )
    return _load_json(PROFILE_SCHEMA_PATH)


def load_catalog() -> Dict[str, Any]:
    if not CATALOG_PATH.exists():
        raise PersonaMappingError(
            f"platform catalog not found at {CATALOG_PATH} "
            "(issue #9 contract; consumed read-only)"
        )
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data or {}


# --------------------------------------------------------------------------- #
# 1. Persona -> AgentProfile materialization
# --------------------------------------------------------------------------- #

# The ten AgentProfile contract fields (issue #9) and the persona-card source
# of each. Profile `owner` is derived, not copied: <tenant>/<id>.
_MATERIALIZED_FIELDS = (
    "id",
    "version",
    "owner",
    "systemPromptRef",
    "toolAllowlist",
    "constraintSet",
    "capabilitySet",
    "defaultModelTier",
    "memoryScope",
    "guardrailPolicyRef",
)


def materialize_profile(
    card: Dict[str, Any],
    tenant: Optional[str] = None,
    validate: bool = True,
) -> Dict[str, Any]:
    """Map a persona card to an AgentProfile (issue #9) and validate it.

    The persona *selects* its prompt (``systemPromptRef``), tools
    (``toolAllowlist``), model tier (``defaultModelTier``) and guardrail
    policy (``guardrailPolicyRef``) from the issue #9/#13 contracts; the
    profile's ``owner`` is derived as ``<tenant>/<id>`` (the tenant is the
    persona's owner namespace). ``validate`` (default on) checks the result
    against the frozen AgentProfile schema + live catalog, so a persona that
    cannot produce a valid profile is rejected at materialization time.
    """
    scope = tenant or card.get("tenant") or _PLATFORM_TENANT
    profile = {
        "id": card["id"],
        "version": card["version"],
        "owner": f"{scope}/{card['id']}",
        "systemPromptRef": card["systemPromptRef"],
        "toolAllowlist": list(card["toolAllowlist"]),
        "constraintSet": list(card.get("constraintSet", [])),
        "capabilitySet": list(card["capabilitySet"]),
        "defaultModelTier": card["defaultModelTier"],
        "memoryScope": list(card["memoryScope"]),
        "guardrailPolicyRef": card["guardrailPolicyRef"],
    }
    if validate:
        validate_profile(profile)
    return profile


def validate_profile(profile: Dict[str, Any]) -> None:
    """Validate a materialized profile against the issue #9 contract.

    Layer 1 is the AgentProfile JSON Schema (closed enums); layer 2 is
    fail-closed membership against the live platform catalog. Raises
    ProfileMaterializationError on the first violation.
    """
    schema = load_profile_schema()
    try:
        jsonschema.validate(instance=profile, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ProfileMaterializationError(
            f"materialized AgentProfile invalid against issue #9 schema: {exc.message}"
        ) from exc
    catalog = load_catalog()
    _check_in(profile, "toolAllowlist", (catalog.get("tools") or {}).keys(), "tool")
    _check_in(
        profile,
        "capabilitySet",
        (catalog.get("capabilities") or {}).keys(),
        "capability",
    )
    _check_in(
        profile,
        "constraintSet",
        (catalog.get("constraints") or {}).keys(),
        "constraint",
    )
    _check_in(profile, "memoryScope", (catalog.get("memoryScopes") or {}).keys(), "memory scope")
    policies = set((catalog.get("guardrailPolicies") or {}).get("atomics") or {})
    policies.update((catalog.get("guardrailPolicies") or {}).get("bundles") or {})
    if profile["guardrailPolicyRef"] not in policies:
        raise ProfileMaterializationError(
            f"unknown guardrailPolicyRef {profile['guardrailPolicyRef']!r} "
            "(not in platform catalog)"
        )
    if profile["defaultModelTier"] not in (catalog.get("tiers") or {}):
        raise ProfileMaterializationError(
            f"unknown defaultModelTier {profile['defaultModelTier']!r} "
            "(not in platform catalog)"
        )


def _check_in(profile: Dict[str, Any], field: str, allowed: Any, label: str) -> None:
    for value in profile.get(field, []):
        if value not in allowed:
            raise ProfileMaterializationError(
                f"unknown {label} {value!r} in materialized profile (not in platform catalog)"
            )


# --------------------------------------------------------------------------- #
# 2. SME reviewer doctrine - separation of duties + assignment
# --------------------------------------------------------------------------- #


def guard_dispatch(card: Dict[str, Any], role: str) -> None:
    """Enforce the posture-class rule for a dispatch.

    ``role`` is one of 'executor' | 'reviewer' | 'auditor'. A persona may only
    be dispatched to the duty matching its posture class - crucially, an
    auditor-posture persona can never be the executing persona. Raises
    SeparationViolationError when the dispatch violates the doctrine.
    """
    posture = card.get("posture")
    if role == "executor":
        if posture == "auditor":
            raise AuditorCannotExecuteError(
                f"persona {card['id']!r} has posture 'auditor' and can never be "
                "the executing persona (separation of duties)"
            )
        if posture != "executor":
            raise SeparationViolationError(
                f"persona {card['id']!r} has posture {posture!r}; only "
                "executor-posture personas execute primary work"
            )
        return
    if role == "reviewer":
        if posture != "reviewer":
            raise SeparationViolationError(
                f"persona {card['id']!r} has posture {posture!r}; only "
                "reviewer-posture personas may act as reviewer (auditors audit)"
            )
        return
    if role == "auditor":
        if posture != "auditor":
            raise SeparationViolationError(
                f"persona {card['id']!r} has posture {posture!r}; only "
                "auditor-posture personas may act as auditor"
            )
        return
    raise PersonaMappingError(f"unknown dispatch role {role!r}")


def assert_reviewer_distinct(executor: Dict[str, Any], reviewer: Dict[str, Any]) -> None:
    """The reviewer of a PR/verdict is never its executor (no self-review)."""
    if _same_identity(executor, reviewer):
        raise SelfReviewError(
            f"reviewer persona {reviewer['id']!r} is the executor of the same "
            "PR/verdict (separation of duties)"
        )


def assert_auditor_not_executor(auditor: Dict[str, Any], executor: Dict[str, Any]) -> None:
    """The auditor persona is never the executing persona of the task it audits."""
    guard_dispatch(executor, "executor")
    if _same_identity(auditor, executor):
        raise SelfAuditError(
            f"auditor persona {auditor['id']!r} cannot be the executing persona "
            "of the task it audits (separation of duties)"
        )


def _same_identity(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return a.get("tenant") == b.get("tenant") and a.get("id") == b.get("id")


def _visible_cards(
    cards: Dict[Tuple[str, str], Dict[str, Any]], tenant: str
) -> List[Dict[str, Any]]:
    """Cards a tenant can see: its own cards plus platform defaults."""
    return [
        card
        for (_t, _p), card in cards.items()
        if _t == tenant or _t == _PLATFORM_TENANT
    ]


def _domain_score(card: Dict[str, Any], subject: str) -> int:
    """Score a persona's fit to a PR/verdict subject.

    Discriminating tokens are the persona id, its owned lanes and its
    expertise phrases. Deterministic and cheap; used to assign the best-fit
    SME reviewer (mirrors the leaderboard lib/lenses discriminating-questions
    model - the assigned persona is the one whose questions change the output).
    """
    hay = subject.lower()
    tokens = [card["id"]] + list(card.get("ownedLanes", [])) + list(card.get("expertise", []))
    score = 0
    for token in tokens:
        lowered = token.lower()
        if lowered and lowered in hay:
            score += 1
    return score


def _best_match(candidates: List[Dict[str, Any]], subject: str) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    scored = sorted(
        ((_domain_score(c, subject), c["id"], c) for c in candidates),
        key=lambda t: (t[0], t[1]),
        reverse=True,
    )
    return scored[0][2]


def assign_reviewer(
    cards: Dict[Tuple[str, str], Dict[str, Any]],
    executor_tenant: str,
    executor_id: str,
    subject: str,
) -> Dict[str, Any]:
    """Assign an independent reviewer persona to a PR/verdict.

    Every PR/verdict gets an assigned reviewer persona (SME reviewer
    doctrine). The assigned reviewer is (a) reviewer posture, (b) visible to
    the executor's tenant, and (c) distinct from the executor - separation of
    duties is structurally guaranteed because executor and reviewer posture
    classes are disjoint. Best-fit is domain-scored; ties break by persona id.
    Raises NoReviewerAvailableError when no reviewer distinct from the
    executor is available.
    """
    visible = _visible_cards(cards, executor_tenant)
    candidates = [c for c in visible if c.get("posture") in _REVIEWER_POSTURES]
    distinct = [
        c
        for c in candidates
        if not (c.get("tenant") == executor_tenant and c["id"] == executor_id)
    ]
    if not distinct:
        raise NoReviewerAvailableError(
            "no reviewer persona distinct from the executor is available "
            "(separation of duties)"
        )
    match = _best_match(distinct, subject)
    # No domain signal matched: fall back to the general reviewer persona so a
    # PR/verdict without a specialist domain still gets a deterministic review.
    if match is not None and _domain_score(match, subject) == 0:
        default = next(
            (c for c in distinct if c["id"] == _DEFAULT_REVIEWER_ID), None
        )
        if default is not None:
            match = default
    return match


def assign_auditor(
    cards: Dict[Tuple[str, str], Dict[str, Any]],
    executor_tenant: str,
    executor_id: str,
    subject: str,
) -> Dict[str, Any]:
    """Assign an auditor persona to audit an executor's claims/evidence.

    Only auditor-posture personas audit; the auditor is always distinct from
    the executor (an auditor can never be the executing persona of the task it
    audits). Raises NoAuditorAvailableError when no auditor is visible.
    """
    visible = _visible_cards(cards, executor_tenant)
    auditors = [c for c in visible if c.get("posture") == "auditor"]
    distinct = [c for c in auditors if not (c.get("tenant") == executor_tenant and c["id"] == executor_id)]
    pool = distinct or auditors
    match = _best_match(pool, subject)
    if match is None:
        raise NoAuditorAvailableError("no auditor persona is available for assignment")
    executor = next(
        (c for c in visible if c["id"] == executor_id and c.get("tenant") == executor_tenant),
        None,
    )
    if executor is not None:
        assert_auditor_not_executor(match, executor)
    return match


# --------------------------------------------------------------------------- #
# CLI demo
# --------------------------------------------------------------------------- #


def _demo() -> int:
    from registry import PersonaRegistry  # type: ignore

    registry = PersonaRegistry()
    cards = registry.discover()
    print(f"persona library: {len(cards)} persona(s) discovered")
    print(f"materializing AgentProfiles for every persona ...")
    profiles = {}
    for (tenant, persona) in sorted(cards):
        profile = materialize_profile(cards[(tenant, persona)])
        profiles[(tenant, persona)] = profile
        print(f"  profile {tenant}/{persona:<16} -> {profile['id']} v{profile['version']} "
              f"tier={profile['defaultModelTier']} policy={profile['guardrailPolicyRef']} "
              f"tools={len(profile['toolAllowlist'])}")
    print("all materialized profiles validated against AgentProfile schema (issue #9)")

    subject = "security review of the tenant isolation change (authnz, fail closed)"
    reviewer = assign_reviewer(cards, _PLATFORM_TENANT, "coder", subject)
    print(f"review assignment: coder PR subject={subject!r}")
    print(f"  -> assigned reviewer persona: {reviewer['id']} (posture={reviewer['posture']})")
    auditor = assign_auditor(cards, _PLATFORM_TENANT, "coder", subject)
    print(f"  -> assigned auditor persona:  {auditor['id']} (posture={auditor['posture']})")

    print("separation-of-duties guards:")
    try:
        guard_dispatch(auditor, "executor")
        print("  FAIL: auditor dispatched as executor was NOT rejected")
        return 1
    except AuditorCannotExecuteError:
        print("  OK: auditor-posture persona refused as executor (AuditorCannotExecuteError)")
    try:
        assert_reviewer_distinct({"tenant": _PLATFORM_TENANT, "id": "coder"}, {"tenant": _PLATFORM_TENANT, "id": "coder"})
        print("  FAIL: self-review was NOT rejected")
        return 1
    except SelfReviewError:
        print("  OK: same persona as executor+reviewer refused (SelfReviewError)")
    return 0


if __name__ == "__main__":
    sys.exit(_demo())
