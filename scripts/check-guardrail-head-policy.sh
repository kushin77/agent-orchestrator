#!/usr/bin/env bash
# check-guardrail-head-policy.sh — hermes/paperclip head-of-org guardrail
# policy proof (issue #951, parent EPIC #878 lanes L9/L10).
#
# guardrails/ had zero references to either provider (#951's gap). This gate
# proves, offline, that the policy declarations closing that gap really
# enforce something:
#
#   * both `hermes-head-guardrails` and `paperclip-operator-guardrails`
#     controls are registered in guardrails/policy/controls.yaml, default OFF
#     (AO-GR-6 flag-gated rollout);
#   * with the controls ON, every forbidden action for each provider is
#     refused BY NAME (the matched rule id names the forbidden action);
#   * every allowed action for each provider passes;
#   * default-deny holds: an action neither allowed nor forbidden falls to
#     BLOCK, uncovered;
#   * the provider flags this policy is gated behind
#     (infra/feature-flags/registry.yaml `enable_paperclip`, and
#     `enable_hermes` once sibling lane #950 lands) are read defensively,
#     read-only, never edited here;
#   * the negative control: removing the hermes policy file
#     (bundles/platform/hermes-head.yaml) from the bundle makes this gate
#     RED — the guardrail is real, not a name that happens to exist.
#
# Exit-code contract (guardrails/honesty tri-state): 0 OK / 1 NOT-OK /
# 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-guardrail-head-policy.sh
#
# ---knowledge---
# module_id: scripts.check-guardrail-head-policy
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, offline-hermetic, feature-flag-gated-off]
# derives_from: null
# owner_sme: security-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#878", "#950", "#951"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-guardrail-head-policy: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0
scratch="/tmp/ao-guardrail-head-policy.$(date +%s%N).$$"
mkdir "$scratch" || exit 2
trap 'rm -rf "$scratch"' EXIT

echo "== both head-of-org controls are registered, default OFF =="
if python3 - <<'PY'
import sys
sys.path.insert(0, "guardrails")
from policy.controls import ControlRegistry
from policy.startup import default_controls_file

reg = ControlRegistry.load_yaml(default_controls_file())
for cid in ("hermes-head-guardrails", "paperclip-operator-guardrails"):
    ctrl = reg.get(cid)
    if ctrl is None:
        raise SystemExit(f"missing control: {cid}")
    if ctrl.enabled:
        raise SystemExit(f"control {cid} ships enabled (must default OFF)")
print("  OK    both controls registered, enabled: false")
PY
then
  :
else
  echo "  FAIL  control registration/default-OFF invariant broken" >&2
  fail=$((fail + 1))
fi

echo "== forbidden actions refused BY NAME, allowed actions pass, default-deny holds =="
if python3 - <<'PY'
import sys
sys.path.insert(0, "guardrails")
from policy import DecisionLevel, PolicyEngine, build_bundle
from policy.controls import ControlRegistry
from policy.startup import default_bundle_dir, default_controls_file

shipped = ControlRegistry.load_yaml(default_controls_file())
target = {"hermes-head-guardrails", "paperclip-operator-guardrails"}


def entry(control):
    e = {
        "id": control.id,
        "name": control.name,
        "description": control.description,
        "enabled": control.id in target,
        "mode": control.mode,
        "implemented_by": list(control.implemented_by),
        "since": control.since,
    }
    if e["enabled"]:
        e["on_since_rationale"] = "gate proof"
    return e


on_registry = ControlRegistry.from_mapping(
    {"version": 1, "controls": [entry(c) for c in shipped.all()]}
)
bundle = build_bundle([default_bundle_dir()], controls=on_registry)
engine = PolicyEngine(bundle, controls=on_registry)

checks = [
    # (action, subject, context, expected_decision, expected_rule_id_or_None)
    ("agent.dispatch", "hermes", {"channel": "gateway"}, DecisionLevel.LOG, "allow-dispatch-via-gateway"),
    ("agent.directive", "hermes", {"channel": "sideband"}, DecisionLevel.BLOCK, "block-directive-off-gateway"),
    ("agent.self_promote", "hermes", {}, DecisionLevel.BLOCK, "block-self-promote"),
    ("rollout.approve", "hermes", {"rollout": {"level": "full"}}, DecisionLevel.BLOCK, "block-full-rollout-approval"),
    ("secrets.access", "hermes", {}, DecisionLevel.BLOCK, "block-secrets-access"),
    ("iac.apply", "hermes", {}, DecisionLevel.BLOCK, "block-iac-apply"),
    ("model.call", "hermes", {"budget": {"utilization_ratio": 1.5}}, DecisionLevel.BLOCK, "block-budget-ceiling-exceeded"),
    ("model.call", "hermes", {"budget": {"utilization_ratio": 0.2}}, DecisionLevel.LOG, "allow-model-call-under-ceiling"),
    ("ticket.update", "paperclip", {}, DecisionLevel.LOG, "allow-ticket-update"),
    ("heartbeat.send", "paperclip", {}, DecisionLevel.LOG, "allow-heartbeat"),
    ("budget.query", "paperclip", {}, DecisionLevel.LOG, "allow-budget-query"),
    ("code.execute", "paperclip", {}, DecisionLevel.BLOCK, "block-code-execution"),
    ("tenant.read", "paperclip", {}, DecisionLevel.BLOCK, "block-cross-tenant-read"),
]

problems = []
for action, subject, ctx, expected, rule_id in checks:
    result = engine.evaluate(action, subject=subject, tenant="acme", context=ctx)
    if result.decision is not expected:
        problems.append(f"{action}/{subject}: expected {expected}, got {result.decision}")
        continue
    got_rules = {hit.rule_id for hit in result.matched_rules}
    if rule_id not in got_rules:
        problems.append(f"{action}/{subject}: expected rule {rule_id!r}, matched {got_rules or None}")

# default-deny: an action neither allowed nor forbidden is uncovered -> BLOCK
for action, subject in (("agent.rewrite_history", "hermes"), ("module.deploy", "paperclip")):
    result = engine.evaluate(action, subject=subject, tenant="acme", context={})
    if not (result.decision is DecisionLevel.BLOCK and result.uncovered):
        problems.append(f"default-deny broken for {action}/{subject}: {result.decision}, uncovered={result.uncovered}")

if problems:
    for p in problems:
        print(f"  FAIL  {p}", file=sys.stderr)
    raise SystemExit(1)
print(f"  OK    {len(checks)} named checks + default-deny hold")
PY
then
  :
else
  echo "  FAIL  head-of-org policy behaviour broken (see above)" >&2
  fail=$((fail + 1))
fi

echo "== provider flags are read defensively, read-only (no infra edit here) =="
flags_file="infra/feature-flags/registry.yaml"
if [ -f "$flags_file" ]; then
  if grep -q "tf_flag: enable_paperclip" "$flags_file"; then
    echo "  OK    enable_paperclip present in $flags_file"
  else
    echo "  FAIL  enable_paperclip not found in $flags_file" >&2
    fail=$((fail + 1))
  fi
  if grep -q "tf_flag: enable_hermes" "$flags_file"; then
    echo "  OK    enable_hermes present in $flags_file (sibling lane #950 landed)"
  else
    echo "  OK    enable_hermes absent from $flags_file (sibling lane #950 not landed yet; read defensively)"
  fi
else
  echo "  OK    $flags_file absent; read defensively (nothing to check here yet)"
fi

echo "== negative control: removing the hermes policy file makes the gate RED =="
mutant_bundle="$scratch/bundle-missing-hermes"
mkdir -p "$mutant_bundle"
cp guardrails/policy/bundles/platform/*.yaml "$mutant_bundle/"
rm -f "$mutant_bundle/hermes-head.yaml"
if python3 - "$mutant_bundle" <<'PY'
import sys
sys.path.insert(0, "guardrails")
from policy import DecisionLevel, PolicyEngine, build_bundle
from policy.controls import ControlRegistry
from policy.startup import default_controls_file

bundle_dir = sys.argv[1]
shipped = ControlRegistry.load_yaml(default_controls_file())
target = {"hermes-head-guardrails", "paperclip-operator-guardrails"}


def entry(control):
    e = {
        "id": control.id,
        "name": control.name,
        "description": control.description,
        "enabled": control.id in target,
        "mode": control.mode,
        "implemented_by": list(control.implemented_by),
        "since": control.since,
    }
    if e["enabled"]:
        e["on_since_rationale"] = "gate negative control"
    return e


registry = ControlRegistry.from_mapping(
    {"version": 1, "controls": [entry(c) for c in shipped.all()]}
)
bundle = build_bundle([bundle_dir], controls=registry)
engine = PolicyEngine(bundle, controls=registry)

# With the hermes policy file removed, a hermes dispatch through the gateway
# — which the (missing) policy would explicitly LOG-allow — must now be
# uncovered and fail closed to BLOCK. If it is still allowed, the gate did
# not actually depend on the hermes policy file, so it must FAIL.
result = engine.evaluate("agent.dispatch", subject="hermes", tenant="acme", context={"channel": "gateway"})
if result.decision is DecisionLevel.BLOCK and result.uncovered:
    print("  OK    hermes actions go uncovered/BLOCK once hermes-head.yaml is removed")
    raise SystemExit(0)
print(f"  FAIL  removing hermes-head.yaml did not turn the gate red (decision={result.decision}, uncovered={result.uncovered})", file=sys.stderr)
raise SystemExit(1)
PY
then
  :
else
  echo "  FAIL  negative control did not fire (gate stayed green without the hermes policy file)" >&2
  fail=$((fail + 1))
fi

echo ""
if [ "$fail" -gt 0 ]; then
  echo "check-guardrail-head-policy: FAIL — $fail finding(s)" >&2
  exit 1
fi
echo "check-guardrail-head-policy: OK — hermes/paperclip guardrails present, default-OFF, default-deny, negative control fires"
exit 0
