"""Honest offline gate for the rollout declarations (issue #45 / work item 41).

Asserts the declarative rollout contract, all of which can genuinely fail
(no-false-green doctrine):

  1. the stage model parses and is a closed, strict-forward vocabulary
     (off -> canary -> gradual -> full, no jumps, rollback target off);
  2. every flag in the current rollout state defaults to OFF (AO-GR-6) - a
     flag that shipped on is a hard failure;
  3. the go-live plan covers every Phase 0-8 surface, each flag resolves and
     each declared stage is in the closed vocabulary;
  4. the rollout Cloud Build triggers ship DISABLED with _ENABLE_ROLLOUT=false
     (flag-gated OFF), mirroring the infra/cloudbuild convention;
  5. service and CI/CD flags in the rollout state mirror
     infra/feature-flags/registry.yaml 1:1 (drift is a finding).

Usage:

    python3 infra/rollout/checks/check_rollout.py            # exit 0/1
    python3 infra/rollout/checks/check_rollout.py --self-test # negative probes

``--self-test`` probes every check on a known-bad input and requires it to
fail; a probe that passes when it should fail exits nonzero, proving the gate
is not a formality.
"""

from __future__ import annotations

import os
import sys

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit(f"check-rollout requires PyYAML ({exc})") from exc

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from infra.rollout.model import (  # noqa: E402
    StageModel,
    validate_go_live_plan_doc,
    validate_rollout_state_doc,
)

ROLLOUT_DIR = os.path.join(_REPO_ROOT, "infra", "rollout")
CLOUDBUILD_DIR = os.path.join(_REPO_ROOT, "infra", "cloudbuild")
REGISTRY = os.path.join(_REPO_ROOT, "infra", "feature-flags", "registry.yaml")

ROLLOUT_TRIGGERS = (
    ("rollout-promote-trigger.yaml", "rollout-promote.yaml"),
    ("rollout-rollback-trigger.yaml", "rollout-rollback.yaml"),
)


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------- #
# Per-check validators (each returns a list of error strings; empty = pass)
# --------------------------------------------------------------------------- #


def check_stage_model(doc: object) -> list:
    errors = []
    try:
        model = StageModel.load(doc)
    except (TypeError, ValueError) as exc:
        return [f"stage-model: {exc}"]
    if model.jump_allowed:
        errors.append("stage-model: promotion_rules.jump_allowed must be false (strict-forward)")
    if model.rollback_target != "off":
        errors.append(f"stage-model: rollback target must be 'off', got '{model.rollback_target}'")
    return errors


def check_rollout_state(doc: object) -> list:
    return [f"rollout-state: {e}" for e in validate_rollout_state_doc(doc)]


def check_go_live_plan(doc: object, known_flags: list, model: StageModel) -> list:
    return [f"go-live-plan: {e}" for e in validate_go_live_plan_doc(doc, known_flags, model)]


def check_registry_parity(state_flags: list, registry: object) -> list:
    errors = []
    if not isinstance(registry, dict):
        return ["registry: infra/feature-flags/registry.yaml must be a mapping"]
    services = registry.get("services")
    ci_cd = registry.get("ci_cd")
    if not isinstance(services, dict) or not isinstance(ci_cd, dict):
        return ["registry: services and ci_cd must be mappings"]
    for flag in state_flags:
        prefix, _, key = flag.partition(".")
        if prefix == "services":
            if key not in services:
                errors.append(f"registry parity: flag '{flag}' has no registry service '{key}'")
        elif prefix == "ci_cd":
            if key not in ci_cd:
                errors.append(f"registry parity: flag '{flag}' has no registry ci_cd entry '{key}'")
        elif prefix == "rollout":
            continue  # rollout-native flags are owned by this lane
        else:
            errors.append(f"registry parity: flag '{flag}' has unknown prefix '{prefix}'")
    return errors


def check_triggers(cloudbuild_dir: str = CLOUDBUILD_DIR) -> list:
    errors = []
    for trigger_name, build_config in ROLLOUT_TRIGGERS:
        path = os.path.join(cloudbuild_dir, trigger_name)
        if not os.path.isfile(path):
            errors.append(f"cloudbuild: missing trigger {trigger_name}")
            continue
        try:
            doc = _load(path)
        except Exception as exc:  # noqa: BLE001 - report any parse failure
            errors.append(f"cloudbuild: {trigger_name} does not parse ({exc})")
            continue
        if not isinstance(doc, dict):
            errors.append(f"cloudbuild: {trigger_name} must be a mapping")
            continue
        if doc.get("disabled") is not True:
            errors.append(f"cloudbuild: {trigger_name} must ship disabled: true (flag-gated OFF)")
        subs = doc.get("substitutions") or {}
        if subs.get("_ENABLE_ROLLOUT") != "false":
            errors.append(f"cloudbuild: {trigger_name} _ENABLE_ROLLOUT must be \"false\"")
        fn = doc.get("filename")
        # `filename` is repo-root-relative, matching the #6 trigger convention
        # (infra/cloudbuild/verify-trigger.yaml points at the same path form).
        fn_path = fn if os.path.isabs(fn) else os.path.join(_REPO_ROOT, fn)
        if not fn or not os.path.exists(fn_path):
            errors.append(f"cloudbuild: {trigger_name} references missing build config '{fn}'")
    return errors


def check_all() -> list:
    """Run every check against the committed files; return all errors."""
    errors = []
    stage_model = _load(os.path.join(ROLLOUT_DIR, "stage-model.yaml"))
    errors.extend(check_stage_model(stage_model))
    model = StageModel.load(stage_model) if not errors else None

    state_doc = _load(os.path.join(ROLLOUT_DIR, "rollout-state.yaml"))
    state_errors = check_rollout_state(state_doc)
    errors.extend(state_errors)
    known_flags = (
        list(state_doc.get("flags", {}).keys()) if isinstance(state_doc, dict) else []
    )

    plan = _load(os.path.join(ROLLOUT_DIR, "go-live-plan.yaml"))
    if model is not None:
        errors.extend(check_go_live_plan(plan, known_flags, model))

    errors.extend(check_registry_parity(known_flags, _load(REGISTRY)))
    errors.extend(check_triggers())
    return errors


# --------------------------------------------------------------------------- #
# Negative probes (--self-test): each must FAIL on a known-bad input
# --------------------------------------------------------------------------- #


def _probes() -> list:
    """(name, errors_fn) where errors_fn returns >=1 error for a bad input."""
    bad_stage = {
        "stages": {
            "off": {"order": 0, "rollout_pct": 0, "exposed": False},
            "canary": {"order": 1, "rollout_pct": 5, "exposed": True},
            "gradual": {"order": 2, "rollout_pct": 100, "exposed": True},
            "full": {"order": 3, "rollout_pct": 100, "exposed": True},
            "oops": {"order": 4, "rollout_pct": 100, "exposed": True},
        },
        "promotion_rules": {"mode": "strict-forward", "every_transition_requires": ["verify_green"]},
    }
    bad_state = {
        "schema_version": 1,
        "flags": {"services.registry": {"stage": "canary", "rollout_pct": 5, "targeted": []}},
    }
    bad_plan = {
        "schema_version": 1,
        "phases": {"0": {"name": "only one", "surfaces": [{"flag": "services.registry", "go_live_stage": "full"}]}},
    }
    bad_registry = {"services": {"gateway": {}}, "ci_cd": {}}
    bad_trigger_dir = os.path.join(ROLLOUT_DIR, "checks")  # no rollout triggers here

    def stage_probe():
        return check_stage_model(bad_stage)

    def state_probe():
        return check_rollout_state(bad_state)

    def plan_probe():
        model = StageModel.load(_load(os.path.join(ROLLOUT_DIR, "stage-model.yaml")))
        return check_go_live_plan(bad_plan, ["services.registry", "ci_cd.verify_trigger", "rollout.pipeline"], model)

    def parity_probe():
        return check_registry_parity(["services.nope", "ci_cd.verify_trigger"], bad_registry)

    def trigger_probe():
        return check_triggers(bad_trigger_dir)

    return [
        ("stage-model rejects unknown stage", stage_probe),
        ("rollout-state rejects default-ON flag", state_probe),
        ("go-live-plan rejects partial phase coverage", plan_probe),
        ("registry parity rejects unknown service flag", parity_probe),
        ("cloudbuild requires disabled rollout triggers", trigger_probe),
    ]


def _run_self_test() -> int:
    print("== check-rollout self-test (negative probes) ==")
    failed = 0
    for name, fn in _probes():
        try:
            errors = fn()
        except Exception as exc:  # noqa: BLE001 - an unexpected exception still fails the probe
            print(f"  FAIL  {name}: probe raised {exc!r} (expected a clean failure)", file=sys.stderr)
            failed += 1
            continue
        if errors:
            print(f"  OK    {name} (correctly failed: {errors[0]})")
        else:
            print(f"  FAIL  {name}: probe PASSED when it must fail (formality)", file=sys.stderr)
            failed += 1
    if failed:
        print(f"check-rollout self-test: {failed} probe(s) failed", file=sys.stderr)
        return 1
    print("check-rollout self-test: OK")
    return 0


def main(argv=None) -> int:
    if "--self-test" in (argv or sys.argv[1:]):
        return _run_self_test()
    errors = check_all()
    for error in errors:
        print(f"  FAIL  {error}", file=sys.stderr)
    if errors:
        print(f"check-rollout: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    print("check-rollout: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
