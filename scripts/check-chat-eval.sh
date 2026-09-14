#!/usr/bin/env bash
# check-chat-eval.sh — chat quality loop enforcement (issue #509).
#
# The chat lane ships three controls: the prompt modules (whose output schemas
# MUST require the citations envelope), the offline eval harness (declared
# expectations that fail by NAME) and the promotion gate (a changed module
# version is re-evaluated against the fixtures before promotion). This gate
# proves each control BITES rather than merely existing:
#
#   * the vocabulary is pinned here and cross-checked against its homes — the
#     guardrail decision levels and the FinOps tier list — so a rename in
#     either fails a gate instead of splitting the vocabulary in two, and the
#     fixture set really declares the five risk kinds and answers each case
#     with the module version the case names;
#   * the committed fixture set is green (5/5) and every module's schema
#     requires the citations envelope;
#   * a mutated expectation fails that case BY NAME — a harness that cannot go
#     red is a formality (GR-12 / AO-GR-19);
#   * a case that cannot be run is CANNOT-ASSESS and is never reported as a
#     pass;
#   * a module schema that only PERMITS the envelope is refused by name;
#   * a candidate version that regresses a case fails the promotion BY NAME,
#     while a candidate that keeps the contract is accepted (no false red).
#
# Every control runs on a scratch copy under /tmp; the lane tree is never
# mutated, and the restored tree is re-checked green at the end.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-chat-eval.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

# ── the pinned vocabulary ───────────────────────────────────────────────────
# Consumed, never re-declared: the decision levels come from
# guardrails/policy/decision.py (DecisionLevel) and the model tiers from
# governance/finops/policy.json (the FinOps tier policy the chooser gate pins).
EXPECT_OUTCOMES="ANSWER, NO_DATA, REFUSAL, BLOCK, FLAG"
EXPECT_GROUNDING="CITED, UNCITED"
EXPECT_TIERS="flash, pro, auditor"
EXPECT_DECISIONS="block, warn, log"
EXPECT_KINDS="grounded, unanswerable, cross-tenant, secret-inbound, poisoned-document"
EXPECT_CASES="grounded-question, unanswerable-question, cross-tenant-probe, secret-carrying-prompt, poisoned-retrieved-document"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-chat-eval: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0

probe() { # probe <name> <want_rc> <must_contain> <must_not_contain> <cmd...>
  local name="$1" want="$2" must="$3" must_not="$4"
  shift 4
  local out rc
  out="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -ne "$want" ]; then
    printf '  FAIL  %s — expected rc=%s, got rc=%s\n' "$name" "$want" "$rc" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
    return 0
  fi
  if [ -n "$must" ] && ! printf '%s' "$out" | grep -qF -- "$must"; then
    printf '  FAIL  %s — rc=%s but the output never says %s\n' "$name" "$rc" "$must" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
    return 0
  fi
  if [ -n "$must_not" ] && printf '%s' "$out" | grep -qF -- "$must_not"; then
    printf '  FAIL  %s — the output must not say %s\n' "$name" "$must_not" >&2
    printf '%s\n' "$out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
    return 0
  fi
  printf '  OK    %s\n' "$name"
}

echo "== files =="
for required in \
  registry/chat/README.md \
  registry/chat/__init__.py \
  registry/chat/labels.py \
  registry/chat/envelope.py \
  registry/chat/prompt_modules.py \
  registry/chat/regression.py \
  registry/chat/feedback.py \
  registry/chat/prompt-module.schema.json \
  registry/chat/manifest.yaml \
  registry/chat/modules/chat-answer.v1.yaml \
  registry/chat/modules/chat-refuse.v1.yaml \
  registry/chat/bodies/chat-answer.v1.system.md \
  registry/chat/bodies/chat-answer.v1.user.md \
  registry/chat/bodies/chat-refuse.v1.system.md \
  registry/chat/bodies/chat-refuse.v1.user.md \
  registry/chat/output-schemas/grounded-answer.schema.json \
  registry/chat/output-schemas/refusal.schema.json \
  registry/chat/eval/README.md \
  registry/chat/eval/cases.yaml \
  registry/chat/eval/harness.py \
  registry/chat/eval/standins.py \
  registry/chat/seed/feedback-events.yaml \
  registry/chat/tests/conftest.py \
  governance/finops/policy.json \
  guardrails/policy/decision.py
do
  if [ -f "$required" ]; then
    echo "  OK    $required present"
  else
    echo "  FAIL  $required is missing" >&2
    fail=$((fail + 1))
  fi
done
if [ "$fail" -gt 0 ]; then
  echo "check-chat-eval: FAIL ($fail missing file(s))" >&2
  exit 1
fi

echo "== vocabulary =="
if python3 - "$EXPECT_OUTCOMES" "$EXPECT_GROUNDING" "$EXPECT_TIERS" "$EXPECT_DECISIONS" \
  "$EXPECT_KINDS" "$EXPECT_CASES" <<'PY'
import importlib.util
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path.cwd()))
expect_outcomes = [part.strip() for part in sys.argv[1].split(",")]
expect_grounding = [part.strip() for part in sys.argv[2].split(",")]
expect_tiers = [part.strip() for part in sys.argv[3].split(",")]
expect_decisions = [part.strip() for part in sys.argv[4].split(",")]
expect_kinds = [part.strip() for part in sys.argv[5].split(",")]
expect_cases = [part.strip() for part in sys.argv[6].split(",")]

problems = []
from registry.chat import labels
from registry.chat.eval import harness, standins
from registry.chat.prompt_modules import GROUNDING_POLICIES, ChatPromptRegistry

if list(labels.OUTCOMES) != expect_outcomes:
    problems.append(f"labels.OUTCOMES = {list(labels.OUTCOMES)}")
if list(labels.GROUNDING) != expect_grounding:
    problems.append(f"labels.GROUNDING = {list(labels.GROUNDING)}")
if list(labels.TIERS) != expect_tiers:
    problems.append(f"labels.TIERS = {list(labels.TIERS)}")
if sorted(labels.DECISION_LEVEL) != sorted(expect_outcomes):
    problems.append(f"DECISION_LEVEL keys = {sorted(labels.DECISION_LEVEL)}")
if sorted(set(labels.DECISION_LEVEL.values())) != sorted(expect_decisions):
    problems.append(f"DECISION_LEVEL values = {sorted(set(labels.DECISION_LEVEL.values()))}")

# One vocabulary, two declarations: the FinOps tier policy and the guardrail
# decision levels are consumed, so a rename in either home must fail here.
policy = json.loads(Path("governance/finops/policy.json").read_text(encoding="utf-8"))
if policy["vocabulary"]["tiers"] != expect_tiers:
    problems.append(f"governance/finops/policy.json tiers = {policy['vocabulary']['tiers']}")
spec = importlib.util.spec_from_file_location(
    "chat_gate_decision", "guardrails/policy/decision.py"
)
decision_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = decision_module
spec.loader.exec_module(decision_module)
levels = sorted(level.value for level in decision_module.DecisionLevel)
if levels != sorted(expect_decisions):
    problems.append(f"guardrails/policy/decision.py DecisionLevel = {levels}")

# The fixtures declare the five risk kinds and name the module version that
# answers each case; the harness cannot check a contract it cannot resolve.
data = harness.load_cases(Path("registry/chat/eval/cases.yaml"))
kinds = [case["kind"] for case in data["cases"]]
case_ids = [case["id"] for case in data["cases"]]
if sorted(kinds) != sorted(expect_kinds):
    problems.append(f"fixture kinds = {sorted(kinds)}")
if case_ids != expect_cases:
    problems.append(f"fixture case ids = {case_ids}")
if sorted(standins.CASE_KINDS) != sorted(expect_kinds):
    problems.append(f"standins.CASE_KINDS = {sorted(standins.CASE_KINDS)}")
registry = ChatPromptRegistry()
for case in data["cases"]:
    expect = case["expect"]
    try:
        resolved = registry.resolve(expect["module"], expect["version"])
    except Exception as exc:  # noqa: BLE001 - any failure means unresolved
        problems.append(
            f"{case['id']}: {expect['module']}@{expect['version']} unresolved ({exc})"
        )
        continue
    if resolved.grounding_policy not in GROUNDING_POLICIES:
        problems.append(
            f"{case['id']}: unknown groundingPolicy {resolved.grounding_policy!r}"
        )


if problems:
    print("  FAIL  the chat vocabulary disagrees across declarations:", file=sys.stderr)
    for problem in problems:
        print(f"        {problem}", file=sys.stderr)
    raise SystemExit(1)
print("  OK    labels, the FinOps tier policy, the guardrail decision levels and the fixture set agree")
PY
then
  :
else
  fail=$((fail + 1))
fi

echo "== the committed tree is green =="
probe "the harness meets every declared expectation" 0 \
  "5/5 case(s) met their declared expectation" "" \
  python3 -m registry.chat.eval.harness --cases registry/chat/eval/cases.yaml
probe "every module's schema requires the citations envelope" 0 \
  "contract: PASS" "" \
  python3 -m registry.chat.prompt_modules contract
probe "the feedback report speaks the prompts view's vocabulary" 0 \
  "total FP=" "" \
  python3 -m registry.chat.feedback report

work="/tmp/ao500-509.$$.$(date +%s)"
mkdir "$work" || exit 2
trap 'rm -rf "$work"' EXIT

fresh_package() { # fresh_package <name> — an unmutated copy for one control
  local destination="$work/$1"
  cp -a registry/chat "$destination"
  find "$destination" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
  printf '%s' "$destination"
}

echo "== negative controls (scratch copies only) =="

python3 - registry/chat/eval/cases.yaml "$work/cases-outcome.yaml" <<'PY'
import sys

import yaml

data = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for case in data["cases"]:
    if case["id"] == "grounded-question":
        case["expect"]["outcome"] = "NO_DATA"
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PY
probe "a mutated expectation fails that case by name" 1 \
  "FAIL grounded-question" "" \
  python3 -m registry.chat.eval.harness --cases "$work/cases-outcome.yaml"

python3 - registry/chat/eval/cases.yaml "$work/cases-unrunnable.yaml" <<'PY'
import sys

import yaml

data = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
data["cases"].append(
    {
        "id": "module-that-does-not-exist",
        "kind": "unanswerable",
        "tenant": "acme",
        "question": "Which module answers this?",
        "context": [],
        "expect": {
            "outcome": "NO_DATA",
            "module": "chat-answer",
            "version": "v9",
            "citations": "empty",
        },
    }
)
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    yaml.safe_dump(data, handle, sort_keys=False)
PY
probe "an unrunnable case is CANNOT-ASSESS, never a pass" 2 \
  "CANNOT-ASSESS module-that-does-not-exist" "PASS module-that-does-not-exist" \
  python3 -m registry.chat.eval.harness --cases "$work/cases-unrunnable.yaml"

schema_package="$(fresh_package chat-schema)"
python3 - registry/chat/output-schemas/grounded-answer.schema.json \
  "$schema_package/output-schemas/grounded-answer.schema.json" <<'PY'
import json
import sys

schema = json.load(open(sys.argv[1], encoding="utf-8"))
schema["required"] = ["answer"]
schema["properties"]["citations"]["minItems"] = 0
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(schema, handle, indent=2)
PY
probe "a schema that only PERMITS the envelope is refused by name" 1 \
  "chat-answer.v1.yaml" "" \
  python3 -m registry.chat.prompt_modules --root "$schema_package" contract

candidate_package="$(fresh_package chat-candidate)"
python3 - "$candidate_package/modules/chat-answer.v1.yaml" \
  "$candidate_package/modules/chat-answer.v2.yaml" answer-without-citations <<'PY'
import sys

import yaml

module = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
module["version"] = "v2"
module["groundingPolicy"] = sys.argv[3]
module["description"] = "a candidate version written by the gate's control"
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    yaml.safe_dump(module, handle, sort_keys=True)
PY
probe "a candidate that regresses a case fails the promotion by name" 1 \
  "REGRESSED grounded-question" "" \
  python3 -m registry.chat.regression --candidate chat-answer@v2 --baseline chat-answer@v1 \
  --cases "$candidate_package/eval/cases.yaml" --root "$candidate_package"

python3 - "$candidate_package/modules/chat-answer.v2.yaml" require-citations <<'PY'
import sys

import yaml

module = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
module["groundingPolicy"] = sys.argv[2]
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    yaml.safe_dump(module, handle, sort_keys=True)
PY
probe "a candidate that keeps the contract is promoted (no false red)" 0 \
  "regresses no fixture case" "" \
  python3 -m registry.chat.regression --candidate chat-answer@v2 --baseline chat-answer@v1 \
  --cases "$candidate_package/eval/cases.yaml" --root "$candidate_package"

echo "== the lane tree was never mutated =="
probe "the restored tree is still green" 0 \
  "5/5 case(s) met their declared expectation" "" \
  python3 -m registry.chat.eval.harness --cases registry/chat/eval/cases.yaml

if [ "$fail" -gt 0 ]; then
  echo "check-chat-eval: FAIL ($fail control(s) did not behave)" >&2
  exit 1
fi
echo "check-chat-eval: OK — vocabulary pinned across 4 declarations, the module contract enforced, 4 provoking controls provoked and 2 non-provocation controls stayed green"
exit 0
