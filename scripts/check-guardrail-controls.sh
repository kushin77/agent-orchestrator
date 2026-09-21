#!/usr/bin/env bash
# check-guardrail-controls.sh — server-side guardrail control semantics (#343).
#
# The shell Controls view only flips real server-side policy state when the
# control surface behind it is honest. This gate proves, offline, that it is:
#
#   * every control defaults OFF (a control that ships ON is refused);
#   * an unknown control id is refused and writes no audit record;
#   * a toggle writes EXACTLY ONE append-only audit record and flips observable
#     state that a SECOND reader (a fresh process) sees (246 -> 446);
#   * the guardrail status vocabulary is the closed Portkey-style pair
#     246 PASSED / 446 BLOCKED, and any other code is refused;
#   * the self-mutating negative control: an ON-by-default control (and a
#     registry entry that ships ON) must be refused — if the mutant is
#     accepted, this check prints FAIL and exits non-zero.
#
# Exit-code contract (guardrails/honesty tri-state): 0 OK / 1 NOT-OK /
# 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-guardrail-controls.sh
#
# ---knowledge---
# module_id: scripts.check-guardrail-controls
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, offline-hermetic, append-only]
# derives_from: null
# owner_sme: security-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#343"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-guardrail-controls: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

cli="guardrails/controls/cli.py"
if [ ! -f "$cli" ]; then
  echo "check-guardrail-controls: CANNOT-ASSESS — $cli not found" >&2
  exit 2
fi

fail=0
scratch="/tmp/ao-guardrail-controls.$(date +%s%N).$"
mkdir "$scratch" || exit 2
trap 'rm -rf "$scratch"' EXIT
state="$scratch/state.json"
audit="$scratch/audit.jsonl"

echo "== invariants: defaults OFF, unknown refused, one audit record, closed 246/446 =="
if python3 "$cli" self-test; then
  echo "  OK    every invariant holds (self-test includes the negative control)"
else
  echo "  FAIL  a control invariant does not hold" >&2
  fail=$((fail + 1))
fi

echo "== an unknown control is refused (and audits nothing) =="
out="$(python3 "$cli" --state "$state" --audit "$audit" toggle no-such-control --on 2>&1)"
rc=$?
audit_lines=0
[ -f "$audit" ] && audit_lines="$(grep -c . "$audit" 2>/dev/null || echo 0)"
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qi 'unknown control' && [ "$audit_lines" -eq 0 ]; then
  echo "  OK    refused (exit $rc) naming the unknown control, no audit written"
else
  echo "  FAIL  unknown control not refused cleanly (exit $rc, audit lines $audit_lines)" >&2
  printf '%s\n' "$out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

echo "== a toggle writes exactly one audit record and flips observable state =="
control_id="$(python3 "$cli" --state "$state" --audit "$audit" list | awk 'NR==2 {print $1}')"
if [ -z "$control_id" ]; then
  if [ "$fail" -eq 0 ]; then
    echo "check-guardrail-controls: CANNOT-ASSESS — could not read a control id" >&2
    exit 2
  fi
  echo "  FAIL  the controls registry refused (see above): no control id readable" >&2
  fail=$((fail + 1))
else
  python3 "$cli" --state "$state" --audit "$audit" toggle "$control_id" --on \
    --actor gate --reason "negative-control proof" >"$scratch/toggle.out" 2>&1
  rc=$?
  audit_lines="$(grep -c . "$audit" 2>/dev/null || echo 0)"
  if [ "$rc" -eq 0 ] && [ "$audit_lines" -eq 1 ]; then
    echo "  OK    one toggle -> exactly one audit record ($control_id)"
  else
    echo "  FAIL  expected one audit record (exit $rc, lines $audit_lines)" >&2
    sed 's/^/        /' "$scratch/toggle.out" >&2
    fail=$((fail + 1))
  fi

  report="$(python3 "$cli" --state "$state" check-report 2>&1)"
  if printf '%s' "$report" | grep -qE "^${control_id}[[:space:]]+446[[:space:]]+BLOCKED"; then
    echo "  OK    a second reader sees the flip: $control_id reports 446 BLOCKED"
  else
    echo "  FAIL  the flipped state was not observable to a second reader" >&2
    printf '%s\n' "$report" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
fi

echo "== the 246/446 vocabulary is closed =="
if python3 - <<'PY'
import sys

sys.path.insert(0, "guardrails")
from controls.model import (  # noqa: E402
    GUARDRAIL_STATUS,
    STATUS_BLOCKED,
    STATUS_PASSED,
    ControlError,
    is_status,
    status_name,
)

assert GUARDRAIL_STATUS == frozenset({STATUS_PASSED, STATUS_BLOCKED})
assert is_status(STATUS_PASSED) and is_status(STATUS_BLOCKED)
assert not is_status(999) and not is_status("246")
assert status_name(STATUS_PASSED) == "PASSED" and status_name(STATUS_BLOCKED) == "BLOCKED"
try:
    status_name(999)
except ControlError:
    pass
else:
    raise SystemExit("status_name accepted an out-of-vocabulary code")
print("  OK    closed {246 PASSED, 446 BLOCKED}; 999 refused")
PY
then
  :
else
  echo "  FAIL  the guardrail status vocabulary is not closed" >&2
  fail=$((fail + 1))
fi

echo "== negative control: a control that ships ON must be refused =="
if python3 "$cli" self-test --mutate default-on; then
  :
else
  echo "  FAIL  the model accepted a control that ships ON (invariant stopped biting)" >&2
  fail=$((fail + 1))
fi

mutant="$scratch/controls.mutant.yaml"
if python3 - "$root/guardrails/policy/controls.yaml" "$mutant" <<'PY'
import sys

src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
anchor = "    enabled: false\n"
if anchor not in text:
    raise SystemExit(f"fixture has no {anchor!r} to mutate")
text = text.replace(
    anchor,
    '    enabled: true\n    on_since_rationale: "gate negative control"\n',
    1,
)
open(dst, "w", encoding="utf-8").write(text)
PY
then
  mutant_out="$(python3 "$cli" --controls "$mutant" --state "$scratch/m.json" --audit "$scratch/m.jsonl" list 2>&1)"
  mutant_rc=$?
  if [ -n "$control_id" ] && [ "$mutant_rc" -ne 0 ] && printf '%s' "$mutant_out" | grep -q "$control_id"; then
    echo "  OK    a registry entry that ships ON is refused (exit $mutant_rc), naming $control_id"
  else
    echo "  FAIL  an ON-by-default control was accepted (exit $mutant_rc)" >&2
    printf '%s\n' "$mutant_out" | sed 's/^/        /' >&2
    fail=$((fail + 1))
  fi
else
  echo "  FAIL  could not build the mutated registry fixture" >&2
  fail=$((fail + 1))
fi

echo ""
if [ "$fail" -gt 0 ]; then
  echo "check-guardrail-controls: FAIL — $fail finding(s)" >&2
  exit 1
fi
echo "check-guardrail-controls: OK — default-OFF controls, audited toggles, closed 246/446"
exit 0
