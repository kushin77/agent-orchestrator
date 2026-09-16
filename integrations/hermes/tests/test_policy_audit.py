"""Policy and audit tests: the `controls` and `audit` faang evidence are real."""

from __future__ import annotations

from pathlib import Path

from integrations.hermes import audit as audit_mod
from integrations.hermes import mapping as mapping_mod
from integrations.hermes import policy as policy_mod

ROOT = Path(__file__).resolve().parents[3]


def test_policy_projects_the_floor_and_thresholds():
    tiers = mapping_mod.read_tiers(ROOT)
    pol = policy_mod.build_policy(tiers, mapping_mod.TIERS_PATH)
    assert pol.floor_tier == "L1"
    assert pol.escalation_thresholds == {"L0": 40.0, "L1": 70.0}
    assert pol.tiers["code-author"].default_tier == "L0"
    assert pol.tiers["code-author"].max_tier == "L1"
    assert pol.tiers["memory-ops"].max_tier == "L0"


def test_audit_reports_every_invariant_held_on_the_clean_tree():
    projection = mapping_mod.build_projection(ROOT)
    entries = audit_mod.audit_projection(projection)
    violations = [entry for entry in entries if entry.status != "held"]
    assert violations == [], violations
    invariants = {entry.invariant for entry in entries}
    assert invariants == {
        "capability-set parity",
        "tier parity",
        "floor presence",
        "tier coverage",
    }
    assert audit_mod.violation_findings(entries) == []
