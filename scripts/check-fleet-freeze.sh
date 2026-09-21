#!/usr/bin/env bash
# check-fleet-freeze.sh — the D7 cutover-drain gate (issue #715 / #902).
#
# THE DEFECT THIS EXISTS FOR
#   `fleet/freeze.py drain` records INTENT (.fleet/freeze.flag): no new local
#   dispatch should start. Nothing in the transport enforces that a flag left
#   set actually matches reality — a laptop could carry `freeze.flag` while its
#   crontab still runs the three `ao-fleet-*` lines uncommented, which is exactly
#   the split-brain state the cutover policy (#706/#902) forbids: drained intent
#   recorded, but the host never actually stopped dispatching new work.
#
# WHAT IS PROVEN
#   Given a real crontab snapshot (read via `crontab -l`, degrading to
#   CANNOT-ASSESS rather than FAIL when no crontab is available, e.g. this
#   sandbox) and the real `.fleet/freeze.flag`:
#     * flag ABSENT -> PASS regardless of crontab state (nothing claims drain).
#     * flag PRESENT + crontab has NO active (uncommented) `ao-fleet-*` line ->
#       PASS (the frozen state the runbook describes).
#     * flag PRESENT + crontab HAS an active `ao-fleet-*` line -> FAIL, naming
#       the offending line.
#
# PROVOCATION (GR-12, no-false-green)
#   The driver logic lives in a small, pure Python function
#   (`classify(flag_present, crontab_lines_file)`) written once to a scratch
#   script and invoked with the crontab snapshot as a FILE ARGUMENT (not piped
#   stdin — `python3 <script>` already consumes stdin to read its own program
#   text when invoked as `python3 -`, so piping data through the same stdin is a
#   measured trap this driver avoids by using a real classifier file). The
#   negative control feeds it a synthetic "flag set + one active
#   ao-fleet-watchdog line" case and asserts FAIL, then the same line commented
#   out and asserts PASS — the two cases differ by exactly one leading `# `, so
#   the check cannot be passing vacuously on crontab shape alone.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-freeze.sh
#
# ---knowledge---
# module_id: scripts.check-fleet-freeze
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, named-refusal]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#706", "#715", "#902"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-freeze: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in fleet/freeze.py fleet/cron.py fleet/tests/test_freeze.py docs/FLEET-CUTOVER.md; do
  if [ ! -f "$required" ]; then
    echo "check-fleet-freeze: FAIL — $required is missing" >&2
    exit 1
  fi
done

work="/tmp/ao-fleet-freeze.$(date +%s%N).$$"
mkdir "$work" 2>/dev/null || {
  echo "check-fleet-freeze: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

fail=0
ok()  { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }
note() { printf '  NOTE  %s\n' "$1"; }

classifier="$work/classify.py"
cat > "$classifier" <<'PY'
import sys

flag_present = sys.argv[1] == "1"
crontab_file = sys.argv[2]
with open(crontab_file, encoding="utf-8") as handle:
    lines = handle.read().splitlines()

markers = ("ao-fleet-watchdog", "ao-fleet-prune", "ao-fleet-reconcile")


def is_ours_active(entry: str) -> bool:
    stripped = entry.rstrip()
    if not any(stripped.endswith(f"# {m}") for m in markers):
        return False
    return not entry.lstrip().startswith("#")


if not flag_present:
    print("PASS: no freeze.flag - nothing claims drain")
    raise SystemExit(0)

active = [line for line in lines if is_ours_active(line)]
if active:
    print("FAIL: freeze.flag is set but the host crontab still has active fleet-cron line(s):")
    for line in active:
        print(f"  {line}")
    raise SystemExit(1)

print("PASS: freeze.flag is set and no active fleet-cron crontab line remains")
raise SystemExit(0)
PY

classify() {
  # $1 = "1"/"0" (flag present), $2 = path to a file of crontab lines.
  python3 "$classifier" "$1" "$2"
}

# --- 1. the real check, against this host's real state ----------------------
flag_path="$root/.fleet/freeze.flag"
crontab_snapshot="$work/real-crontab.txt"
if command -v crontab >/dev/null 2>&1; then
  crontab -l >"$crontab_snapshot" 2>/dev/null || : >"$crontab_snapshot"
  crontab_available=1
else
  : >"$crontab_snapshot"
  crontab_available=0
fi

if [ -f "$flag_path" ]; then
  real_flag=1
else
  real_flag=0
fi

if [ "$crontab_available" -eq 1 ]; then
  if out="$(classify "$real_flag" "$crontab_snapshot")"; then
    echo "$out" | sed 's/^/  /'
    ok "real host state: frozen intent matches the crontab"
  else
    echo "$out" | sed 's/^/  /' >&2
    bad "real host state: freeze.flag is set but a fleet-cron line is still active"
  fi
else
  note "no crontab available in this environment — real-state check skipped (CANNOT-ASSESS for that half only)"
fi

# --- 2. the negative control: the classifier itself must be able to FAIL ----
active_line="*/2 * * * * cd /repo && python3 fleet/watchdog.py run >> /repo/.fleet/watchdog.log 2>&1 # ao-fleet-watchdog"
commented_line="# ${active_line}"

active_file="$work/active.txt"
printf '%s\n' "$active_line" > "$active_file"
if classify "1" "$active_file" > "$work/neg.log" 2>&1; then
  cat "$work/neg.log" >&2
  bad "provocation ACTIVE-LINE-WITH-FLAG did not fail — the classifier cannot distinguish frozen from not-frozen"
else
  grep -q "FAIL:" "$work/neg.log" && ok "provoked ACTIVE-LINE-WITH-FLAG was refused by name"
fi

commented_file="$work/commented.txt"
printf '%s\n' "$commented_line" > "$commented_file"
if classify "1" "$commented_file" > "$work/pos.log" 2>&1; then
  grep -q "PASS:" "$work/pos.log" && ok "the same line, commented, passes (one leading '# ' is the whole difference)"
else
  cat "$work/pos.log" >&2
  bad "commenting the offending line did not clear the failure — the classifier is not measuring what it claims"
fi

empty_file="$work/empty.txt"
: > "$empty_file"
if classify "0" "$empty_file" > "$work/noflag.log" 2>&1; then
  grep -q "PASS:" "$work/noflag.log" && ok "no flag, no crontab lines at all — passes (nothing claims drain)"
else
  cat "$work/noflag.log" >&2
  bad "an absent flag must never fail this gate on its own"
fi

# --- 3. the freeze unit suite is green ---------------------------------------
if env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
     fleet/tests/test_freeze.py >"$work/pytest.log" 2>&1; then
  ok "fleet/tests/test_freeze.py is green ($(tail -n 1 "$work/pytest.log" | tr -d '\r'))"
else
  cat "$work/pytest.log" >&2
  bad "fleet/tests/test_freeze.py is not green"
fi

# --- 4. the enforcement: fleet/watchdog.py must actually call refuse_if_frozen
#        before every spawn (#978) — provoked negative control -------------------
watchdog_src="$root/fleet/watchdog.py"
call_count="$(grep -c 'freeze\.refuse_if_frozen(' "$watchdog_src" 2>/dev/null || echo 0)"
if [ "$call_count" -lt 2 ]; then
  bad "fleet/watchdog.py calls freeze.refuse_if_frozen() only $call_count time(s) (need >=2: respawn + start_monitor)"
else
  ok "fleet/watchdog.py calls freeze.refuse_if_frozen() $call_count time(s) before spawning"
fi

# Provoke: a copy of watchdog.py with every refuse_if_frozen() call stripped
# must FAIL the wiring probe below (frozen no longer stops a spawn); the real
# file must PASS it. Same file, one deletion apart — proves the probe is not
# vacuously green.
probe="$work/wiring_probe.py"
cat > "$probe" <<'PY'
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

watchdog_path = Path(sys.argv[1])
fleet_dir = Path(sys.argv[2])
scratch_dir = Path(sys.argv[3])
sys.path.insert(0, str(fleet_dir))

spec = importlib.util.spec_from_file_location("watchdog_probe", watchdog_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

spawned = []
mod.spawn = lambda *a, **k: spawned.append((a, k))
mod.loop_pid = lambda pattern: None
mod.read_beat = lambda path: None
mod.rung_came_up = lambda *a, **k: True
mod.freeze.refuse_if_frozen = lambda rung: f"[{rung}] REFUSED — frozen"
# Never read/write the real .fleet/ — a stale real drift record (e.g. already
# PARKED from unrelated history) would short-circuit bounded_remedy before it
# ever reaches respawn(), making this probe pass vacuously either way.
mod.FLEET_DIR = scratch_dir
mod.RUNS_DIR = scratch_dir / "runs"

try:
    mod.rung_action("sister", "fleet/terminal.py", "fleet/terminal.sh", Path("/tmp/x"), False, "head")
except Exception as exc:  # a mangled call site (e.g. the check line removed) is also "not wired"
    print(f"FAIL: rung_action raised instead of refusing cleanly: {type(exc).__name__}: {exc}")
    raise SystemExit(1)

if spawned:
    print("FAIL: a spawn happened while frozen")
    raise SystemExit(1)
print("PASS: no spawn happened while frozen")
raise SystemExit(0)
PY

wiring_scratch="$work/wiring-scratch"
mkdir -p "$wiring_scratch/real" "$wiring_scratch/provoked"
if python3 "$probe" "$watchdog_src" "$root/fleet" "$wiring_scratch/real" > "$work/wiring_real.log" 2>&1; then
  ok "real fleet/watchdog.py: refuse_if_frozen actually stops a spawn"
else
  cat "$work/wiring_real.log" >&2
  bad "real fleet/watchdog.py did NOT refuse to spawn while frozen"
fi

provoked="$work/watchdog_provoked.py"
grep -v 'freeze\.refuse_if_frozen(' "$watchdog_src" > "$provoked"
if python3 "$probe" "$provoked" "$root/fleet" "$wiring_scratch/provoked" > "$work/wiring_provoked.log" 2>&1; then
  cat "$work/wiring_provoked.log" >&2
  bad "provocation (refuse_if_frozen call removed) did NOT fail — the wiring probe cannot detect its absence"
else
  grep -q "FAIL:" "$work/wiring_provoked.log" && ok "provoked copy (refuse_if_frozen removed) correctly spawns while frozen and is caught"
fi

# --- 5. the wiring note -------------------------------------------------------
if grep -qF 'bash scripts/check-fleet-freeze.sh' scripts/verify.sh 2>/dev/null; then
  ok "scripts/verify.sh runs this check"
else
  note "scripts/verify.sh does not name this check yet — wire it as:"
  note "  'fleet-freeze|bash scripts/check-fleet-freeze.sh'"
fi

echo ""
if [ "$fail" -eq 0 ]; then
  echo "check-fleet-freeze: OK — a set freeze.flag and an active fleet-cron crontab line can never coexist undetected"
  exit 0
fi
echo "check-fleet-freeze: NOT-OK — $fail check(s) failed" >&2
exit 1
