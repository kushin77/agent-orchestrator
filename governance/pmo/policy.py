"""Load + validate ``governance/pmo/policy.yaml`` (issue #403 follow-on).

The priority/dispatch policy is declared data (docs/SME-ROUTING.md's own
doctrine: "the policy is declared data, not code"), not restated in Python.
This module reads it, validates it against ``policy.schema.json`` (structural)
plus a couple of semantic invariants JSON Schema cannot express, and hands back
a small read-only :class:`Policy` object. A malformed policy is
:class:`~graph.CannotAssess` — never a silent default, exactly like a graph
that will not build.

---knowledge---
module_id: governance.pmo.policy
system: governance
app: pmo
solution_class: enterprise
patterns: [declared-policy]
derives_from: null
owner_sme: pmo-sme
tier: L1
interfaces: [Lane, Policy, load]
invariants: "a policy that cannot be assessed is CannotAssess (rc 2), never a silent default"
gotchas: ""
related: []
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft7Validator

from graph import CannotAssess

POLICY_RELPATH = "governance/pmo/policy.yaml"
SCHEMA_RELPATH = "governance/pmo/policy.schema.json"


@dataclass(frozen=True)
class Lane:
    pillar: str
    lane: str
    owns: tuple[str, ...]
    tier: str
    sme: str
    #: SME-ROUTING-style override of the ADR-0012(b) kind-based default
    #: executor persona ("hermes" / "paperclip"). Empty means "no override" —
    #: dispatch.py falls back to the kind-based default.
    executor: str = ""


@dataclass(frozen=True)
class Policy:
    """The validated policy document, plus the lookups the views need."""

    raw: dict[str, Any]
    wave_cap_default: int
    priority_weights: dict[str, float]
    aging_weights: dict[str, float]
    fanout_weight_per_blocker: float
    readiness_bonus: float
    capacity_penalty_per_inflight: float
    unsourced_terms: dict[str, dict[str, Any]]
    model_tiers: dict[str, str]
    lanes: tuple[Lane, ...]
    default_lane: Lane

    def priority_weight(self, labels: tuple[str, ...]) -> tuple[float, str]:
        """The P-level weight for a set of board labels, and the level named."""
        for level in ("P0", "P1", "P2"):
            if f"priority:{level}" in labels:
                return self.priority_weights[level], level
        return self.priority_weights["unset"], "unset"

    def aging_weight(self, tier: str) -> float:
        return self.aging_weights.get(tier or "none", self.aging_weights["none"])

    def lane_for(self, labels: tuple[str, ...]) -> Lane:
        for lane in self.lanes:
            if lane.pillar in labels:
                return lane
        return self.default_lane


def _lane_from(payload: dict[str, Any], pillar: str = "") -> Lane:
    return Lane(
        pillar=pillar or payload.get("pillar", ""),
        lane=payload["lane"],
        owns=tuple(payload.get("owns") or ()),
        tier=payload["tier"],
        sme=payload["sme"],
        executor=payload.get("executor", ""),
    )


def load(root: Path | str = ".") -> Policy:
    root = Path(root)
    policy_path = root / POLICY_RELPATH
    schema_path = root / SCHEMA_RELPATH
    # The policy is versioned inside this package, so a fixture root without
    # its own copy still resolves it (mirrors graph._load_ticket_builder's
    # "read from the repo when the checkout carries none" fallback).
    if not policy_path.is_file():
        policy_path = Path(__file__).resolve().parent / "policy.yaml"
    if not schema_path.is_file():
        schema_path = Path(__file__).resolve().parent / "policy.schema.json"

    try:
        document = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CannotAssess(f"pmo policy missing: {POLICY_RELPATH}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise CannotAssess(f"pmo policy unreadable: {POLICY_RELPATH} ({exc})") from exc

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CannotAssess(f"pmo policy schema unreadable: {SCHEMA_RELPATH} ({exc})") from exc

    if not isinstance(document, dict):
        raise CannotAssess(f"pmo policy is not a mapping: {POLICY_RELPATH}")

    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    if errors:
        rendered = "; ".join(f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors[:5])
        raise CannotAssess(f"pmo policy fails schema {SCHEMA_RELPATH}: {rendered}")

    # semantic invariants schema alone cannot express -----------------------
    lane_names = [entry["lane"] for entry in document["lanes"]]
    if len(lane_names) != len(set(lane_names)):
        raise CannotAssess("pmo policy declares the same lane twice")
    pillars = [entry["pillar"] for entry in document["lanes"]]
    if len(pillars) != len(set(pillars)):
        raise CannotAssess("pmo policy maps two lanes to the same pillar label")
    for entry in document["lanes"]:
        if entry["tier"] not in document["model_tiers"]:
            raise CannotAssess(f"pmo policy lane {entry['lane']!r} names an undeclared tier {entry['tier']!r}")
    if document["default_lane"]["tier"] not in document["model_tiers"]:
        raise CannotAssess("pmo policy default_lane names an undeclared tier")

    return Policy(
        raw=document,
        wave_cap_default=int(document["wave_cap_default"]),
        priority_weights=dict(document["priority_weights"]),
        aging_weights=dict(document["aging_weights"]),
        fanout_weight_per_blocker=float(document["fanout_weight_per_blocker"]),
        readiness_bonus=float(document["readiness_bonus"]),
        capacity_penalty_per_inflight=float(document["capacity_penalty_per_inflight"]),
        unsourced_terms=dict(document["unsourced_terms"]),
        model_tiers=dict(document["model_tiers"]),
        lanes=tuple(_lane_from(entry) for entry in document["lanes"]),
        default_lane=_lane_from(document["default_lane"], pillar=""),
    )
