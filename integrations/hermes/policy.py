"""The hermes tiering policy, mapped read-only from the FinOps tier table (issue #942).

ADR-0012 splits the hermes boundary in two: *map the policy, do not couple the
runtime*. This module is the policy half. It reads the FinOps tier table that
already governs the fleet — ``gateway/finops/tiers.yaml``'s per-capability
cheapest-capable default tier and escalation cap, the security floor, and the
complexity escalation thresholds — and projects them into one frozen value the
mapper consumes, so the projection never re-implements a rule a source file
already declares.

The policy is a **floor, never a ceiling** (ADR-0012 decision (b)): the
security floor outranks the persona tier, and a caller may always route *above*
the floor. This module records the floor and the thresholds verbatim; it decides
no routing — that stays with the fleet brain.

Stdlib-only by construction (frozen dataclasses + typing), so neither the tests
nor the gate pull a third-party dependency in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping

#: The closed task-class tier vocabulary the tier table uses (L0..L2).
TIER_VOCABULARY = ("L0", "L1", "L2")


@dataclass(frozen=True)
class TaskClass:
    """One ``taskClasses`` entry: a capability's default tier and escalation cap."""

    capability: str
    default_tier: str
    max_tier: str


@dataclass(frozen=True)
class HermesPolicy:
    """The mapped tiering policy: floor, thresholds, and per-capability tiers."""

    floor_tier: str
    escalation_thresholds: Mapping[str, float]
    tiers: Mapping[str, TaskClass]
    source: str


def build_policy(tiers: Dict[str, Any], source: str) -> HermesPolicy:
    """Project the tiering policy out of an already-parsed ``tiers.yaml``.

    ``tiers`` is the dict ``mapping.read_tiers`` returns; ``source`` names the
    file it came from so the projection can cite its provenance.
    """
    task_classes = tiers.get("taskClasses") or {}
    tier_map: Dict[str, TaskClass] = {}
    for cap, entry in task_classes.items():
        if isinstance(entry, dict):
            tier_map[str(cap)] = TaskClass(
                capability=str(entry.get("capability") or cap),
                default_tier=str(entry.get("defaultTier") or ""),
                max_tier=str(entry.get("maxTier") or ""),
            )

    security = tiers.get("security") or {}
    escalation = tiers.get("escalation") or {}
    thresholds = {
        str(key): value
        for key, value in (escalation.get("thresholds") or {}).items()
    }
    return HermesPolicy(
        floor_tier=str(security.get("floorTier") or ""),
        escalation_thresholds=thresholds,
        tiers=tier_map,
        source=source,
    )


def tier_projection(
    policy: HermesPolicy, capabilities: Iterable[str]
) -> Dict[str, Dict[str, str]]:
    """The ``tiering.capabilities`` block: one entry per capability, in order."""
    out: Dict[str, Dict[str, str]] = {}
    for cap in capabilities:
        entry = policy.tiers.get(cap)
        out[cap] = {
            "default_tier": entry.default_tier if entry else "",
            "max_tier": entry.max_tier if entry else "",
        }
    return out
