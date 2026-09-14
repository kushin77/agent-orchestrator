#!/usr/bin/env bash
# check-sme-routing.sh — SME-squad routing + route/tier FinOps contract (issue #149).
#
# Standalone gate for the gateway/sme-routing surface. It is deliberately NOT
# wired into scripts/verify.sh (a shared file): the orchestrator wires it after
# this issue merges.
#
# What it proves, by running the shipped CLI and reading the real exit codes:
#
#   * the three policies load, satisfy schema.yaml and every declared
#     invariant (validate -> 0);
#   * a fast-path, a deep-path and a strict-path dispatch are each demonstrated,
#     with the tier and chain the policy declares (demo -> 0);
#   * an unknown task type takes the declared deep fail-safe;
#   * a token-cap breach is an explicit refusal under --no-escalate (rc 1) and
#     escalates to the declared higher tier when escalation is authorised;
#   * escalation with no higher tier terminates at the distinct human_advisor
#     outcome, bounded by the tier count;
#   * the gate is not vacuous: three policy variants in a scratch directory must
#     move the outcome (re-point the fail-safe -> rc 1, break the shape -> rc 2,
#     neutralise the risk keywords -> the strict route must stop being chosen).
#     If any variant still produces the shipped answer, this check FAILS.
#   * the shipped policies are read-only inputs: their sha256 is identical
#     before and after the run.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-sme-routing.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

module="gateway/sme-routing"
cli="$module/cli.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-sme-routing: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

fail=0

note_ok()   { printf '  OK    %s\n' "$1"; }
note_fail() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

# contains <haystack> <needle> <label>
contains() {
  case "$1" in
    *"$2"*) note_ok "$3" ;;
    *) note_fail "$3 — expected '$2' in: $1" ;;
  esac
}

# --- 1. required files ------------------------------------------------------
echo "== files =="
for required in \
  "$cli" \
  "$module/router.py" \
  "$module/smeroute_config.py" \
  "$module/jsonschema_lite.py" \
  "$module/schema.yaml" \
  "$module/policies/capability-registry.yaml" \
  "$module/policies/route-policy.yaml" \
  "$module/policies/tier-policy.yaml" \
  "$module/tests/conftest.py" \
  "docs/SME-ROUTING.md"
do
  if [ -f "$required" ]; then
    note_ok "$required present"
  else
    note_fail "$required is missing"
  fi
done
if [ "$fail" -gt 0 ]; then
  echo "check-sme-routing: CANNOT-ASSESS — $fail required file(s) missing" >&2
  exit 2
fi

# --- 2. the policies are read-only inputs -----------------------------------
policies_dir="$module/policies"
before_hash="$(find "$policies_dir" -name '*.yaml' -print0 \
  | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum)"

# --- 3. helpers -------------------------------------------------------------
route_summary() {
  python3 -c '
import json, sys
d = json.load(sys.stdin)
print("route=%s tier=%s path_mode=%s chain=%s sme=%s squad=%s module=%s fail_safe=%s" % (
    d["route"], d["tier"], d["path_mode"], ",".join(d["chain"]),
    d["sme"], d["squad"], d["module"] or "-",
    "yes" if d["fail_safe"] else "no"))
'
}

dispatch_summary() {
  python3 -c '
import json, sys
d = json.load(sys.stdin)
print("status=%s tier=%s escalated=%s attempts=%d tried=%s" % (
    d["status"], d["tier"], "yes" if d["escalated"] else "no",
    len(d["attempts"]), ",".join(a["tier"] for a in d["attempts"])))
'
}

# run <policies-dir-or-empty> <args...>  -> sets $out and $rc
run_cli() {
  local dir="$1"
  shift
  if [ -n "$dir" ]; then
    out="$(python3 "$cli" --policies "$dir" "$@" 2>&1)"
  else
    out="$(python3 "$cli" "$@" 2>&1)"
  fi
  rc=$?
}

# --- 4. validate ------------------------------------------------------------
echo "== validate =="
run_cli "" validate
if [ "$rc" -eq 0 ]; then
  note_ok "validate -> rc 0"
  contains "$out" "fail-safe unknown_task_type=deep" "the deep fail-safe is declared"
else
  note_fail "validate -> rc $rc (expected 0)"
  printf '%s\n' "$out" | sed 's/^/        /' >&2
fi

# --- 5. demo: the three paths ----------------------------------------------
echo "== demo =="
run_cli "" demo
if [ "$rc" -eq 0 ]; then
  note_ok "demo -> rc 0"
  contains "$out" "fast-path dispatch" "fast path demonstrated"
  contains "$out" "deep-path dispatch" "deep path demonstrated"
  contains "$out" "strict-path dispatch" "strict path demonstrated"
  contains "$out" "human/advisor hand-off" "human/advisor terminal demonstrated"
else
  note_fail "demo -> rc $rc (expected 0)"
  printf '%s\n' "$out" | sed 's/^/        /' >&2
fi

# --- 6. the routing contract ------------------------------------------------
echo "== routing contract =="
run_cli "" route --json --type doc_update --text "tighten the README wording" --tokens 40
line="$(printf '%s' "$out" | route_summary)"
contains "$line" "route=fast" "fast path chosen for a declared cheap type"
contains "$line" "tier=flash" "fast path runs on the cheapest tier"
contains "$line" "chain=executor" "fast path carries the single-agent chain"

run_cli "" route --json --text "refactor the loader across multi-file modules"
line="$(printf '%s' "$out" | route_summary)"
contains "$line" "route=deep" "complexity keyword routes to deep"
contains "$line" "tier=pro" "deep path runs on the pro tier"
contains "$line" "chain=planner,executor,verifier" "deep path carries the plan/execute/verify chain"

run_cli "" route --json --text "rotate the production secret credential"
line="$(printf '%s' "$out" | route_summary)"
contains "$line" "route=strict" "risk keyword routes to strict"
contains "$line" "tier=auditor" "strict path runs on the auditor tier"
contains "$line" "chain=planner,executor,verifier,critic" "strict path carries the governance chain"

run_cli "" route --json --type teleport --text "do the undeclared thing" --tokens 5
line="$(printf '%s' "$out" | route_summary)"
contains "$line" "route=deep" "unknown task type defaults to deep"
contains "$line" "fail_safe=yes" "the deep default is flagged fail-safe"

# --- 7. the cost controls ---------------------------------------------------
echo "== tier caps =="
run_cli "" dispatch --json --type doc_update --text "neutral" --tokens 40
line="$(printf '%s' "$out" | dispatch_summary)"
contains "$line" "status=dispatched" "a request within the caps dispatches"
contains "$line" "tier=flash" "and does so on the routed tier"
if [ "$rc" -ne 0 ]; then note_fail "in-caps dispatch -> rc $rc (expected 0)"; fi

run_cli "" dispatch --json --type doc_update --text "neutral" --tokens 9000 --no-escalate
line="$(printf '%s' "$out" | dispatch_summary)"
contains "$line" "status=refused" "a token-cap breach is refused, not silently passed"
contains "$out" "token cap breached" "the refusal names the breached cap"
if [ "$rc" -eq 1 ]; then
  note_ok "cap breach without escalation -> rc 1"
else
  note_fail "cap breach without escalation -> rc $rc (expected 1)"
fi

run_cli "" dispatch --json --type doc_update --text "neutral" --tokens 9000
line="$(printf '%s' "$out" | dispatch_summary)"
contains "$line" "status=dispatched" "an authorised escalation resolves the breach"
contains "$line" "tier=pro" "escalation lands on the declared fallback tier"
contains "$line" "tried=flash,pro" "the ladder records every rung it climbed"

run_cli "" dispatch --json --type doc_update --text "neutral" --tokens 40000
line="$(printf '%s' "$out" | dispatch_summary)"
contains "$line" "status=human_advisor" "no higher tier terminates at the human/advisor hand-off"
contains "$line" "tier=auditor" "the terminal rung is the top tier"
contains "$line" "tried=flash,pro,auditor" "the ladder is bounded by the tier count"
if [ "$rc" -eq 0 ]; then
  note_ok "the human/advisor terminal is an outcome, not a crash (rc 0)"
else
  note_fail "human/advisor terminal -> rc $rc (expected 0)"
fi

# --- 8. non-vacuity: the controls must actually move the outcome ------------
echo "== non-vacuity (policy variants in a scratch directory) =="
scratch="/tmp/sme-routing.$(date +%s%N).$$"
if ! mkdir -p "$scratch"; then
  echo "check-sme-routing: CANNOT-ASSESS — cannot create $scratch" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

python3 - "$policies_dir" "$scratch" <<'PY'
import pathlib
import shutil
import sys

import yaml

src = pathlib.Path(sys.argv[1])
scratch = pathlib.Path(sys.argv[2])


def copy_all(dest):
    dest.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.glob("*.yaml")):
        shutil.copyfile(path, dest / path.name)
    return dest


def rewrite(dest, name, document):
    (dest / name).write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


# A: re-point the declared fail-safe at the cheap path.
variant_a = copy_all(scratch / "variant-a")
doc = yaml.safe_load((variant_a / "route-policy.yaml").read_text(encoding="utf-8"))
doc["dispatch_defaults"]["unknown_task_type"] = "fast"
rewrite(variant_a, "route-policy.yaml", doc)

# B: break the declared shape (drop a required field).
variant_b = copy_all(scratch / "variant-b")
doc = yaml.safe_load((variant_b / "capability-registry.yaml").read_text(encoding="utf-8"))
del doc["agents"]
rewrite(variant_b, "capability-registry.yaml", doc)

# C: neutralise the risk keyword list.
variant_c = copy_all(scratch / "variant-c")
doc = yaml.safe_load((variant_c / "route-policy.yaml").read_text(encoding="utf-8"))
doc["thresholds"]["risk_high_keywords"] = ["zzz-never-matches"]
rewrite(variant_c, "route-policy.yaml", doc)
PY

run_cli "$scratch/variant-a" validate
if [ "$rc" -eq 1 ]; then
  note_ok "re-pointed fail-safe -> NOT-OK (rc 1), so the fail-safe is enforced"
else
  note_fail "re-pointed fail-safe -> rc $rc (expected 1; a pass here would be vacuous)"
fi

run_cli "$scratch/variant-b" validate
if [ "$rc" -eq 2 ]; then
  note_ok "broken shape -> CANNOT-ASSESS (rc 2), so schema validation bites"
else
  note_fail "broken shape -> rc $rc (expected 2; a pass here would be vacuous)"
fi

run_cli "$scratch/variant-c" route --json --text "rotate the production secret credential"
line="$(printf '%s' "$out" | route_summary)"
if [ "$rc" -eq 0 ]; then
  note_ok "the neutralised-keyword variant still routes (rc 0)"
else
  note_fail "the neutralised-keyword variant -> rc $rc (expected 0)"
fi
case "$line" in
  *"route=strict"*)
    note_fail "neutralising risk_high_keywords left the strict route chosen: the keyword list is not load-bearing" ;;
  *)
    note_ok "emptying risk_high_keywords changes the route, so the keyword list is load-bearing ($line)" ;;
esac

# --- 9. the shipped policies were not rewritten -----------------------------
echo "== read-only inputs =="
after_hash="$(find "$policies_dir" -name '*.yaml' -print0 \
  | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum)"
if [ "$before_hash" = "$after_hash" ]; then
  note_ok "the shipped policies are byte-identical after the run (${after_hash%% *})"
else
  note_fail "the shipped policies changed during the run: $before_hash -> $after_hash"
fi

# --- summary ----------------------------------------------------------------
if [ "$fail" -ne 0 ]; then
  echo "check-sme-routing: NOT-OK — $fail finding(s)" >&2
  exit 1
fi
echo "check-sme-routing: OK — routing, caps, escalation terminal and non-vacuity proven"
exit 0
