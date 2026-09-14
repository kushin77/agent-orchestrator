#!/usr/bin/env bash
# check-fleet-runbook.sh — the session-fleet bootstrap runbook gate (M26, #166),
# extended with the capability-drift runbook (#319).
#
# The bootstrap claim (#166) is: the only human action in the whole model is
# switching the sister session's model to DSv4FNone; everything else — channel
# init, mailbox setup, dispatcher start, claim of the next issue, verification,
# merge, and recovery from every named failure mode — is code. That claim is
# only true if a gate fails when the runbook regresses (no-false-green
# doctrine, GR-12). This check pins fleet/README.md:
#
#   * the three-step bootstrap (start sister session, set the model, sister
#     runs the loop) and the "ONLY human step" framing;
#   * the standing directive fleet/directive.json is the artifact read on
#     startup — the file must exist and be non-empty;
#   * the Recovery section: sister-dies/claim-TTL take-over, mailbox backlog,
#     and dispatcher failure (rc=2 -> brain alerted via fleet/health.py).
#
# The capability-drift half (#319) is the runbook for a control that ships but
# is not live: docs/FLEET-CAPABILITY-DRIFT.md must keep declaring the restart
# step and the three cases (down / drifted code / current code missing a
# declared capability), and this check PROVOKES each one against synthetic
# beats and REQUIRES it to be reported, naming every missing capability. A
# report that stops naming one turns this check red (GR-12); it reads no live
# fleet state and writes none.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-runbook.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

readme="fleet/README.md"
directive="fleet/directive.json"
health_py="fleet/health.py"
drift_doc="docs/FLEET-CAPABILITY-DRIFT.md"

for required_file in "$readme" "$directive" "$health_py" "$drift_doc"; do
  if [ ! -f "$required_file" ]; then
    echo "check-fleet-runbook: FAIL — $required_file is missing" >&2
    exit 1
  fi
done

if [ ! -s "$directive" ]; then
  echo "check-fleet-runbook: FAIL — $directive is empty (sister has no standing order to read)" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-runbook: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

# Distinctive text the runbook must keep declaring. Removing a concept (or
# renaming it out of existence) fails this gate by name, not silently.
declare -a required_markers=(
  "the ONLY human step"
  "## Recovery"
  "ttl_hours"
  "reaped_agent"
  ".fleet/done"
  "2 failing"
  "1 degraded"
  "fleet/health.py check"
)

fail=0
for marker in "${required_markers[@]}"; do
  if ! grep -qF -- "$marker" "$readme"; then
    printf '  FAIL  %s (missing runbook text: %s)\n' "$readme" "$marker" >&2
    fail=1
  fi
done

if [ "$fail" -eq 0 ]; then
  echo "  OK    $readme declares the human-free bootstrap and the recovery paths"
else
  fail=1
fi

# Non-vacuous: the check must detect a mutated runbook with recovery stripped.
mutant="$(mktemp)"
trap 'rm -f "$mutant"' EXIT
grep -v "^## Recovery" "$readme" | grep -vF "ttl_hours" | grep -vF "reaped_agent" > "$mutant"

mutant_fail=0
for marker in "${required_markers[@]}"; do
  if ! grep -qF -- "$marker" "$mutant"; then
    mutant_fail=1
    break
  fi
done
if [ "$mutant_fail" -ne 1 ]; then
  echo "check-fleet-runbook: FAIL — the mutant (recovery section stripped) was not detected; gate is vacuous" >&2
  fail=1
fi

# --- capability drift (#319): the restart step must stay documented ----------
declare -a drift_markers=(
  "CAPABILITY STALE"
  "capabilities_version"
  "rung DOWN"
  "rung on DRIFTED CODE"
  "rung on CURRENT code MISSING a declared capability"
  "bash fleet/terminal.sh"
  "bash fleet/brain.sh"
  "python3 fleet/watchdog.py capabilities"
  "will **NOT** fix"
)

for marker in "${drift_markers[@]}"; do
  if ! grep -qF -- "$marker" "$drift_doc"; then
    printf '  FAIL  %s (missing capability-drift text: %s)\n' "$drift_doc" "$marker" >&2
    fail=1
  fi
done
if [ "$fail" -eq 0 ]; then
  echo "  OK    $drift_doc declares the restart step and all three drift cases"
fi

# --- provocation: each case must be reported, naming every missing capability
work="$(mktemp -d)"
trap 'rm -f "$mutant"; rm -rf "$work"' EXIT

head_sha="$(git rev-parse --short HEAD 2>/dev/null || true)"
first_sha="$(git rev-list --max-parents=0 HEAD 2>/dev/null | head -1 || true)"
if [ -z "$head_sha" ] || [ -z "$first_sha" ]; then
  echo "check-fleet-runbook: CANNOT-ASSESS — git history is unavailable" >&2
  exit 2
fi

declared_ids="$(python3 - <<'PY'
import sys
sys.path.insert(0, "fleet")
import channel
print(" ".join(cap.id for cap in channel.capabilities_for_rung("sister")))
PY
)"
if [ -z "$declared_ids" ]; then
  echo "check-fleet-runbook: FAIL — fleet/channel.py declares no capability for the sister" >&2
  exit 1
fi

now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 - "$work" "$head_sha" "$first_sha" "$now" <<'PY'
import json
import sys

work, head_sha, first_sha, now = sys.argv[1:5]
beats = {
    # case 2: live, on a commit that predates every declared capability.
    "drifted.json": {"pid": 1, "state": "idle", "commit": first_sha, "ts": now},
    # case 3: on HEAD, declaring a list that omits a declared capability.
    "stale.json": {
        "pid": 1,
        "state": "idle",
        "commit": head_sha,
        "ts": now,
        "capabilities_version": 1,
        "capabilities": [],
    },
}
for name, beat in beats.items():
    with open(f"{work}/{name}", "w", encoding="utf-8") as handle:
        json.dump(beat, handle)
PY

report() { # report <case> <beat>
  python3 fleet/watchdog.py capabilities --rung sister --beat "$2" --head "$head_sha"
}

require_text() { # require_text <case> <text> <haystack>
  if ! grep -qF -- "$2" <<<"$3"; then
    printf '  FAIL  provoked %s was not reported: missing %s\n' "$1" "$2" >&2
    fail=1
    return 1
  fi
  return 0
}

# case 1 — rung DOWN: no beat at all, its own remediation.
down_out="$(report down "$work/absent.json" 2>&1)"
down_rc=$?
if [ "$down_rc" -eq 0 ]; then
  printf '  FAIL  a rung with no beat was reported healthy:\n%s\n' "$down_out" >&2
  fail=1
else
  require_text "DOWN" "rung DOWN" "$down_out" &&
    require_text "DOWN" "bash fleet/run-fleet.sh" "$down_out" &&
    echo "  OK    provoked DOWN reported with its own remediation"
fi

# case 2 — drifted code: every declared capability that commit predates is named.
drift_out="$(report drifted "$work/drifted.json" 2>&1)"
drift_rc=$?
if [ "$drift_rc" -eq 0 ]; then
  printf '  FAIL  a rung on a pre-capability commit was reported healthy:\n%s\n' "$drift_out" >&2
  fail=1
else
  drift_ok=1
  require_text "drifted" "rung on DRIFTED CODE" "$drift_out" || drift_ok=0
  require_text "drifted" "CAPABILITY STALE" "$drift_out" || drift_ok=0
  require_text "drifted" "BETWEEN runs" "$drift_out" || drift_ok=0
  for cap_id in $declared_ids; do
    require_text "drifted" "$cap_id" "$drift_out" || drift_ok=0
  done
  [ "$drift_ok" -eq 1 ] && echo "  OK    provoked DRIFTED CODE names each missing capability"
fi

# case 3 — current code, missing a declared capability: named, and a restart
# must NOT be offered as the fix.
stale_out="$(report stale "$work/stale.json" 2>&1)"
stale_rc=$?
if [ "$stale_rc" -eq 0 ]; then
  printf '  FAIL  a rung on HEAD that does not declare a capability was reported healthy:\n%s\n' "$stale_out" >&2
  fail=1
else
  stale_ok=1
  require_text "missing capability" "rung on CURRENT code MISSING a declared capability" "$stale_out" || stale_ok=0
  require_text "missing capability" "CAPABILITY STALE" "$stale_out" || stale_ok=0
  require_text "missing capability" "will NOT fix" "$stale_out" || stale_ok=0
  for cap_id in $declared_ids; do
    require_text "missing capability" "$cap_id" "$stale_out" || stale_ok=0
  done
  [ "$stale_ok" -eq 1 ] && echo "  OK    provoked missing capability names each one and refuses the restart remedy"
fi

if [ "$fail" -ne 0 ]; then
  echo "check-fleet-runbook: NOT-OK" >&2
  exit 1
fi

echo "check-fleet-runbook: OK"
exit 0
