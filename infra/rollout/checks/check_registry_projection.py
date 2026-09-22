"""Honest offline gate for the registry projection (issue #967).

``infra/rollout/registry_projection.py`` is the pure function that answers
"has this promotion reached the surface declaration the portal reads yet?".
This gate is its control: it refuses, BY NAME, a registry whose
``surfaces.<name>.promoted`` field has not caught up with a real promotion
recorded in ``infra/rollout/live-state.yaml`` — the "last manual step" #967
measured nobody was checking for.

Exit-code contract (repo convention, GR-12): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

Usage::

    python3 infra/rollout/checks/check_registry_projection.py            # exit 0/1/2
    python3 infra/rollout/checks/check_registry_projection.py --self-test # negative probe

---knowledge---
module_id: infra.rollout.checks.check_registry_projection
system: infra
app: rollout
solution_class: class
patterns: [pre-standard-snapshot]
derives_from: null
owner_sme: iac-sme
tier: L1
interfaces: [check_projection, check_all, main]
invariants: ""
gotchas: ""
related: ["#1911"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import os
import sys

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"CANNOT-ASSESS: check-registry-projection requires PyYAML ({exc})")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from infra.rollout.registry_projection import (  # noqa: E402
    project_all,
    registry_matches_projection,
)

ROLLOUT_DIR = os.path.join(_REPO_ROOT, "infra", "rollout")
LIVE_STATE = os.path.join(ROLLOUT_DIR, "live-state.yaml")
GO_LIVE_PLAN = os.path.join(ROLLOUT_DIR, "go-live-plan.yaml")
REGISTRY = os.path.join(_REPO_ROOT, "infra", "feature-flags", "registry.yaml")


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def check_projection(live_state_doc, plan_doc, registry_doc) -> list:
    """Every plan-named surface whose promotion the registry has not caught up with."""
    registry_surfaces = registry_doc.get("surfaces") if isinstance(registry_doc, dict) else None
    errors = []
    for surface, projected in project_all(live_state_doc, plan_doc).items():
        if not registry_matches_projection(registry_surfaces, surface, projected):
            errors.append(
                f"registry-projection-drift: surfaces.{surface} is promoted at stage "
                f"'{projected['stage']}' in live-state.yaml but "
                f"infra/feature-flags/registry.yaml declares 'promoted: "
                f"{(registry_surfaces or {}).get(surface, {}).get('promoted') if isinstance(registry_surfaces, dict) else None}' "
                "- project the promotion into the registry declaration (or disclose it as "
                "the manual step, gated, so it cannot be assumed done)"
            )
    return errors


def check_all() -> list:
    if not os.path.isfile(LIVE_STATE):
        # Absent live-state is the normal, honest "nothing promoted yet" case
        # (infra/rollout/live-state.yaml's own header) - never a finding.
        live_state_doc = {"schema_version": 1, "flags": {}}
    else:
        live_state_doc = _load(LIVE_STATE)
    plan_doc = _load(GO_LIVE_PLAN)
    registry_doc = _load(REGISTRY)
    return check_projection(live_state_doc, plan_doc, registry_doc)


def _run_self_test() -> int:
    """Plant a promotion in a SCRATCH live-state; a stale registry must be refused BY NAME."""
    print("== check-registry-projection self-test (negative probe) ==")
    plan_doc = _load(GO_LIVE_PLAN)
    registry_doc = _load(REGISTRY)
    registry_surfaces = registry_doc.get("surfaces") if isinstance(registry_doc, dict) else {}
    # Any real surfaces.* the plan names and the registry still declares
    # `promoted: false` for is a valid probe target - today every one of them
    # is (nothing has been promoted yet), so this never silently no-ops.
    candidate = None
    from infra.rollout.registry_projection import plan_surface_names

    for name in sorted(plan_surface_names(plan_doc)):
        entry = registry_surfaces.get(name) if isinstance(registry_surfaces, dict) else None
        if not (isinstance(entry, dict) and entry.get("promoted") is True):
            candidate = name
            break
    if candidate is None:
        print("  FAIL  no plan-named surface is available to probe (registry projects everything already)", file=sys.stderr)
        return 1

    scratch_live_state = {
        "schema_version": 1,
        "flags": {
            f"surfaces.{candidate}": {
                "stage": "full",
                "from_stage": "gradual",
                "since": "2026-09-18T00:00:00Z",
                "approval_id": "self-test-probe",
                "audit_record": "audit/2026-09-16-go-live-607-promotion-attempt.md",
            }
        },
    }
    errors = check_projection(scratch_live_state, plan_doc, registry_doc)
    hit = [e for e in errors if f"surfaces.{candidate}" in e and "registry-projection-drift" in e]
    if not hit:
        print(f"  FAIL  planting a full promotion for surfaces.{candidate} was NOT refused (formality): {errors!r}", file=sys.stderr)
        return 1
    print(f"  OK    registry-projection-drift fires by name: {hit[0]}")

    # Vacuity check: the same scratch live-state against a registry the
    # projection ALREADY matches (candidate's row patched to promoted: true)
    # must produce no finding for that surface - proves the gate is not
    # unconditionally red.
    import copy

    patched = copy.deepcopy(registry_doc)
    patched.setdefault("surfaces", {}).setdefault(candidate, {})["promoted"] = True
    clean_errors = check_projection(scratch_live_state, plan_doc, patched)
    if any(f"surfaces.{candidate}" in e for e in clean_errors):
        print(f"  FAIL  a matching registry row still produced a finding (vacuity broken): {clean_errors!r}", file=sys.stderr)
        return 1
    print(f"  OK    a registry row that matches the projection produces no finding for surfaces.{candidate}")
    print("check-registry-projection self-test: OK")
    return 0


def main(argv=None) -> int:
    if "--self-test" in (argv or sys.argv[1:]):
        return _run_self_test()
    try:
        errors = check_all()
    except FileNotFoundError as exc:
        print(f"CANNOT-ASSESS: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"CANNOT-ASSESS: {exc}", file=sys.stderr)
        return 2
    for error in errors:
        print(f"  FAIL  {error}", file=sys.stderr)
    if errors:
        print(f"check-registry-projection: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    print("check-registry-projection: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
