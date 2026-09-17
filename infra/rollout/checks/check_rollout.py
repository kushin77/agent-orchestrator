"""Honest offline gate for the rollout declarations (issue #45 / work item 41).

Asserts the declarative rollout contract, all of which can genuinely fail
(no-false-green doctrine):

  1. the stage model parses and is a closed, strict-forward vocabulary
     (off -> canary -> gradual -> full, no jumps, rollback target off);
  2. every flag in the current rollout state defaults to OFF (AO-GR-6) - a
     flag that shipped on is a hard failure;
  3. the go-live plan covers every Phase 0-8 surface, each flag resolves and
     each declared stage is in the closed vocabulary - and the plan/state
     pairing holds in BOTH directions (issue #966): a flag the plan names but
     the state does not declare is a typo, and a flag the state declares but
     no phase names is a row the ordered ladder can never reach;
  4. the rollout Cloud Build triggers ship DISABLED with _ENABLE_ROLLOUT=false
     (flag-gated OFF), mirroring the infra/cloudbuild convention;
  5. service and CI/CD flags in the rollout state mirror
     infra/feature-flags/registry.yaml 1:1 (drift is a finding);
  6. the plan's declared ``promotion_order`` is the one the ordered go-live
     driver (#619) honours. It was read by NO code before that driver, so a
     plan that quietly declared something else would have been an order nobody
     implemented - now it is a gate failure.

Usage:

    python3 infra/rollout/checks/check_rollout.py            # exit 0/1
    python3 infra/rollout/checks/check_rollout.py --self-test # negative probes

``--self-test`` probes every check on a known-bad input and requires it to
fail; a probe that passes when it should fail exits nonzero, proving the gate
is not a formality.
"""

from __future__ import annotations

import copy
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
    RolloutStage,
    StageModel,
    validate_go_live_plan_doc,
    validate_live_state_doc,
    validate_rollout_state_doc,
)

ROLLOUT_DIR = os.path.join(_REPO_ROOT, "infra", "rollout")
CLOUDBUILD_DIR = os.path.join(_REPO_ROOT, "infra", "cloudbuild")
REGISTRY = os.path.join(_REPO_ROOT, "infra", "feature-flags", "registry.yaml")
LIVE_STATE = os.path.join(ROLLOUT_DIR, "live-state.yaml")

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
    if model.approval_policy is not None and model.approval_policy.auto_approves(RolloutStage.FULL):
        errors.append("stage-model: policy_auto_approve must not include 'full' (human-gated apply)")
    return errors


def check_rollout_state(doc: object) -> list:
    return [f"rollout-state: {e}" for e in validate_rollout_state_doc(doc)]


def check_live_state(doc: object, known_flags: list, model: StageModel, rollout_dir: str = ROLLOUT_DIR) -> list:
    """Structural (``validate_live_state_doc``) + I/O errors for live-state.yaml.

    The structural/ordering rules are the pure model function; this adds the
    one thing that function cannot check without I/O: every entry's
    ``audit_record`` must resolve to a real file under ``infra/rollout/``
    (audit/ or approvals/) - a promoted stage with no provable audit trail
    is a finding, not a formality.
    """
    errors = [f"live-state: {e}" for e in validate_live_state_doc(doc, known_flags, model)]
    if not isinstance(doc, dict):
        return errors
    flags = doc.get("flags")
    if not isinstance(flags, dict):
        return errors
    for name, raw in flags.items():
        if not isinstance(raw, dict):
            continue
        audit_record = raw.get("audit_record")
        if not isinstance(audit_record, str) or not audit_record:
            continue  # already flagged as missing by validate_live_state_doc
        candidate = audit_record if os.path.isabs(audit_record) else os.path.join(rollout_dir, audit_record)
        if not os.path.isfile(candidate):
            errors.append(f"live-state: flag '{name}' audit_record '{audit_record}' does not exist")
    return errors


def _plan_named_flags(doc: object) -> set:
    """Every flag some phase of the plan names (empty for an unusable plan)."""
    named: set = set()
    if not isinstance(doc, dict):
        return named
    phases = doc.get("phases")
    if not isinstance(phases, dict):
        return named
    for body in phases.values():
        if not isinstance(body, dict):
            continue
        surfaces = body.get("surfaces")
        if not isinstance(surfaces, list):
            continue
        for surface in surfaces:
            if isinstance(surface, dict) and isinstance(surface.get("flag"), str):
                named.add(surface["flag"])
    return named


def check_state_reachability(state_flags: list, plan_doc: object) -> list:
    """The REVERSE direction of the plan/state pairing (issue #966).

    ``validate_go_live_plan_doc`` answers exactly one question - does every
    flag the plan names resolve in the rollout state? It never asked the other
    one: is every flag the state declares **reached by some phase**? So a flag
    could be declared, drivable (the engine promotes a flag it carries) and
    **permanently unreachable** (the ordered driver takes its path from this
    plan), with the only symptom an operator noticing that a go-live run never
    mentioned it. That is the defect measured on ``services.erp_module`` and
    ``surfaces.erp_module`` - the same class as #954 and #935, one level up:
    the gate was one-directional, and the direction it did not check is the one
    that decides whether a surface can ever ship.

    The two directions are deliberately worded apart because they take
    different fixes: a row no phase names belongs **in a phase** (or needs a
    reasoned exemption recorded beside it), while a name with no declaration is
    a misspelling in the plan.

    An unusable plan (no phase names any flag) reports nothing here: it is
    already a hard error from ``validate_go_live_plan_doc``, and repeating it
    once per state flag would bury the reason rather than add to it.
    """
    named = _plan_named_flags(plan_doc)
    if not named:
        return []
    return [
        f"go-live-plan: flag '{flag}' is declared in rollout-state.yaml (so the engine can drive it) "
        "but is named by no phase of go-live-plan.yaml, so the ordered ladder can never reach it - "
        "add it to the phase it belongs to, or record a reasoned exemption beside its row"
        for flag in sorted(set(state_flags) - named)
    ]


def check_go_live_plan(doc: object, known_flags: list, model: StageModel) -> list:
    errors = [f"go-live-plan: {e}" for e in validate_go_live_plan_doc(doc, known_flags, model)]
    if isinstance(doc, dict) and doc.get("promotion_order") != "strict-by-phase":
        errors.append(
            "go-live-plan: promotion_order must be 'strict-by-phase' - the ordered go-live driver "
            "(infra/rollout/go_live.py) computes and enforces that order, so a plan declaring anything "
            "else declares an order no code implements"
        )
    errors.extend(check_state_reachability(known_flags, doc))
    return errors


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
        elif prefix == "surfaces":
            # A `surfaces.*` flag gates a route family inside a service (issue
            # #802: the console). Its lock-step partner is the registry's own
            # `surfaces` section, not `services` — and without this branch the
            # parity rule rejected the prefix outright, which would have made
            # adding a surfaces row impossible rather than checked.
            surfaces = registry.get("surfaces")
            if not isinstance(surfaces, dict):
                errors.append("registry parity: the registry declares no surfaces section")
            elif key not in surfaces:
                errors.append(f"registry parity: flag '{flag}' has no registry surface '{key}'")
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

    if os.path.isfile(LIVE_STATE):
        live_state_doc = _load(LIVE_STATE)
    else:
        live_state_doc = {"schema_version": 1, "flags": {}}
    if model is not None:
        errors.extend(check_live_state(live_state_doc, known_flags, model))

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
    bad_full_policy = {
        "stages": {
            "off": {"order": 0, "rollout_pct": 0, "exposed": False},
            "canary": {"order": 1, "rollout_pct": 5, "exposed": True},
            "gradual": {"order": 2, "rollout_pct": 100, "exposed": True},
            "full": {"order": 3, "rollout_pct": 100, "exposed": True},
        },
        "promotion_rules": {
            "mode": "strict-forward",
            "every_transition_requires": ["verify_green"],
            "policy_auto_approve": {
                "policy": "low-risk-auto-approve",
                "targets": ["canary", "gradual", "full"],
            },
        },
    }

    def stage_probe():
        return check_stage_model(bad_stage)

    def full_autoapprove_probe():
        return check_stage_model(bad_full_policy)

    def state_probe():
        return check_rollout_state(bad_state)

    def plan_probe():
        model = StageModel.load(_load(os.path.join(ROLLOUT_DIR, "stage-model.yaml")))
        return check_go_live_plan(bad_plan, ["services.registry", "ci_cd.verify_trigger", "rollout.pipeline"], model)

    def promotion_order_probe():
        """A plan whose declared order no code honours must fail BY NAME.

        The probe keeps full phase coverage (unlike ``bad_plan``) so it fires
        the promotion_order rule alone rather than the coverage rule.
        """
        model = StageModel.load(_load(os.path.join(ROLLOUT_DIR, "stage-model.yaml")))
        plan = copy.deepcopy(_load(os.path.join(ROLLOUT_DIR, "go-live-plan.yaml")))
        plan["promotion_order"] = "whatever-order"
        known = list(_load(os.path.join(ROLLOUT_DIR, "rollout-state.yaml"))["flags"])
        errors = check_go_live_plan(plan, known, model)
        if any("promotion_order" in error for error in errors):
            return errors[:1]
        return [f"the promotion_order rule did not fire: {errors!r}"]

    def parity_probe():
        return check_registry_parity(["services.nope", "ci_cd.verify_trigger"], bad_registry)

    def surface_parity_probe():
        """A `surfaces.*` state flag with no registry surface must fail BY NAME.

        The probe insists on the SURFACE branch's own message: a checker that
        merely rejected the prefix would also return "some error" and would not
        be asserting the lock-step this lane added, so it would pass vacuously.
        """
        errors = check_registry_parity(["surfaces.nope"], _load(REGISTRY))
        if any("has no registry surface 'nope'" in error for error in errors):
            return errors[:1]
        return [f"the surfaces parity branch did not fire: {errors!r}"]

    def trigger_probe():
        return check_triggers(bad_trigger_dir)

    def live_state_missing_audit_probe():
        model = StageModel.load(_load(os.path.join(ROLLOUT_DIR, "stage-model.yaml")))
        bad_live_state = {
            "schema_version": 1,
            "flags": {
                "services.registry": {
                    "stage": "canary",
                    "from_stage": "off",
                    "since": "2026-09-16T00:00:00Z",
                    "policy": "low-risk-auto-approve",
                    "audit_record": "audit/does-not-exist.md",
                }
            },
        }
        return check_live_state(bad_live_state, ["services.registry"], model)

    def live_state_full_policy_probe():
        model = StageModel.load(_load(os.path.join(ROLLOUT_DIR, "stage-model.yaml")))
        bad_live_state = {
            "schema_version": 1,
            "flags": {
                "services.registry": {
                    "stage": "full",
                    "from_stage": "gradual",
                    "since": "2026-09-16T00:00:00Z",
                    "policy": "low-risk-auto-approve",
                    "audit_record": "audit/some-record.md",
                }
            },
        }
        return check_live_state(bad_live_state, ["services.registry"], model)

    def live_state_non_adjacent_probe():
        model = StageModel.load(_load(os.path.join(ROLLOUT_DIR, "stage-model.yaml")))
        bad_live_state = {
            "schema_version": 1,
            "flags": {
                "services.registry": {
                    "stage": "full",
                    "from_stage": "off",
                    "since": "2026-09-16T00:00:00Z",
                    "approval_id": "a-full",
                    "audit_record": "audit/some-record.md",
                }
            },
        }
        return check_live_state(bad_live_state, ["services.registry"], model)

    def unplanned_state_flag_probe():
        """A declared, drivable flag that no phase reaches must fail BY NAME (#966).

        The reverse direction: the plan resolves every flag it names, and the
        state declares one more row, which the ordered ladder can therefore
        never reach. The probe insists on THIS direction's own message, so the
        one-directional check (the defect #966 measured) cannot satisfy it by
        reporting some other error.
        """
        model = StageModel.load(_load(os.path.join(ROLLOUT_DIR, "stage-model.yaml")))
        plan = _load(os.path.join(ROLLOUT_DIR, "go-live-plan.yaml"))
        known = list(_load(os.path.join(ROLLOUT_DIR, "rollout-state.yaml"))["flags"])
        errors = check_go_live_plan(plan, [*known, "rollout.probe_orphan"], model)
        if any("named by no phase" in error for error in errors):
            return errors[:1]
        return [f"the declared-but-not-planned rule did not fire: {errors!r}"]

    def undeclared_plan_flag_probe():
        """A phase naming a flag the state does not declare must fail BY NAME.

        The opposite direction - a misspelling in the plan rather than a row no
        phase reaches. The probe asserts THIS direction's message, because the
        two have different meanings and different fixes, so one reported as the
        other would misdirect the repair.
        """
        model = StageModel.load(_load(os.path.join(ROLLOUT_DIR, "stage-model.yaml")))
        plan = copy.deepcopy(_load(os.path.join(ROLLOUT_DIR, "go-live-plan.yaml")))
        plan["phases"]["7"]["surfaces"].append(
            {"flag": "services.probe_undeclared", "go_live_stage": "full"}
        )
        known = list(_load(os.path.join(ROLLOUT_DIR, "rollout-state.yaml"))["flags"])
        errors = check_go_live_plan(plan, known, model)
        if any("does not resolve to a known flag" in error for error in errors):
            return errors[:1]
        return [f"the planned-but-not-declared rule did not fire: {errors!r}"]

    return [
        ("stage-model rejects unknown stage", stage_probe),
        ("stage-model rejects policy auto-approving full", full_autoapprove_probe),
        ("rollout-state rejects default-ON flag", state_probe),
        ("go-live-plan rejects partial phase coverage", plan_probe),
        ("go-live-plan rejects a promotion_order no driver honours", promotion_order_probe),
        ("go-live-plan rejects a declared flag no phase reaches", unplanned_state_flag_probe),
        ("go-live-plan rejects a phase naming an undeclared flag", undeclared_plan_flag_probe),
        ("registry parity rejects unknown service flag", parity_probe),
        ("registry parity checks a surfaces flag against the surfaces section", surface_parity_probe),
        ("cloudbuild requires disabled rollout triggers", trigger_probe),
        ("live-state rejects a missing audit_record file", live_state_missing_audit_probe),
        ("live-state rejects full with a policy approval (not human)", live_state_full_policy_probe),
        ("live-state rejects a non-adjacent stage jump", live_state_non_adjacent_probe),
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
