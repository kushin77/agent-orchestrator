#!/usr/bin/env bash
# check-runtime-liveness.sh — the runtime-liveness gate (issue #1271).
#
# Every runtime beats through `integrations/paperclip/adapters/heartbeat`
# (`{runtime, commit, state, ts}`), registered in `fleet/runtimes.yaml`. This
# gate gathers the real inputs (beats under `.fleet/runtime-beats/`, git
# distance to `origin/master`, directives in flight under `.fleet/inbox/`) and
# hands them to the PURE judge in `fleet/runtime_liveness.py::judge`, which is what
# `--self-test` provokes directly.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. Findings are named
# `runtime-stale:<id>`, `runtime-drift:<id>`, `runtime-unregistered:<id>`.
#
# It also carries the LEAK CONTROL for #1459 ("the leak control" below): a beat
# this tree did not earn is a finding here, so a check that drives the real spawn
# path without redirecting the beat producer (#1412) would make this gate's
# verdict depend on what ran before it. The control provokes that, and requires
# the judgement to name it — so the property cannot regress silently.
#
# Usage:
#   bash scripts/check-runtime-liveness.sh              # judge the real tree
#   bash scripts/check-runtime-liveness.sh --self-test   # negative controls
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-runtime-liveness: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

verb="run"
for arg in "$@"; do
  case "$arg" in
    --self-test) verb="self-test" ;;
  esac
done

# --- the leak control (#1459) -------------------------------------------------
#
# This gate judges the beats THIS tree holds, so a beat the tree did not earn is a
# finding. #1459 measured the consequence: two checks drove the real spawn path
# without redirecting the producer (#1412), each left `deepseek-executor.json`
# behind, and this judge then reported every OTHER registered runtime
# `runtime-stale` — the composite's verdict depended on which check ran before it.
# The redirects live in those two checks (`check-fleet-runner-preflight.sh`,
# `check-spawn-envelope.sh`); this control is what makes their absence loud, and it
# can fail:
#
#   1. IN THIS TREE: a beat for an id `fleet/runtimes.yaml` does not declare is
#      named `runtime-unregistered:<id>` and this gate goes red. An UNREGISTERED
#      probe is deliberate — this control must never overwrite a live runtime's own
#      beat — and it is removed again here, with the removal and the judgement's
#      silence about it both asserted, before the real judgement below runs.
#   2. IN A SCRATCH TREE: ONE beat the tree did not earn is enough to make every
#      runtime with no beat `runtime-stale:<id>` — the exact chain #1459 measured,
#      reproduced where it cannot touch the repository.
#
# Neither arm can pass by doing nothing: arm 1 asserts the finding BY NAME, and
# arm 2 asserts both that the chain reproduces and that removing the single beat
# leaves the same tree inert.
fail=0

judge_at() { # judge_at <tree-to-judge> — the judge's own verdict on that tree
  env PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" python3 -m fleet.runtime_liveness run --root "$1"
}

probe_dir="$root/.fleet/runtime-beats"
probe_id="leak-control-probe"
probe_file="$probe_dir/$probe_id.json"
scratch=""
made_dir=0
[ -d "$probe_dir" ] || made_dir=1

cleanup_probe() {
  rm -f "$probe_file"
  if [ "$made_dir" -eq 1 ]; then
    rmdir "$probe_dir" 2>/dev/null || true
    rmdir "$(dirname "$probe_dir")" 2>/dev/null || true
  fi
  [ -n "$scratch" ] && rm -rf "$scratch" || true
}
trap cleanup_probe EXIT

if ! mkdir -p "$probe_dir" 2>/dev/null; then
  echo "check-runtime-liveness: CANNOT-ASSESS — cannot create $probe_dir" >&2
  exit 2
fi

judge_at "$root" >/dev/null 2>&1
base_rc=$?
printf '{"commit":"%s","runtime":"%s","state":"running","ts":%s}\n' \
  "$(git -C "$root" rev-parse HEAD 2>/dev/null || printf 'liveness-leak-control')" \
  "$probe_id" "$(date +%s)" > "$probe_file" || exit 2
provoked_out="$(judge_at "$root" 2>&1)"
provoked_rc=$?
if [ "$provoked_rc" -eq 1 ] && [[ "$provoked_out" == *"runtime-unregistered:$probe_id"* ]]; then
  echo "  OK    the leak control bites: a beat this tree did not earn reds the judge, BY NAME"
else
  echo "  FAIL  the leak control did not bite: a stray beat in $probe_dir answered rc=$provoked_rc without naming runtime-unregistered:$probe_id" >&2
  printf '%s\n' "$provoked_out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi

cleanup_probe
if [ -e "$probe_file" ]; then
  echo "  FAIL  the leak control left its probe at $probe_file — that stray beat is the very finding this control exists to prevent" >&2
  fail=$((fail + 1))
fi
back_out="$(judge_at "$root" 2>&1)"
back_rc=$?
if [[ "$back_out" == *"$probe_id"* ]]; then
  echo "  FAIL  the judge still reports the control's probe after its removal" >&2
  fail=$((fail + 1))
else
  echo "  OK    the control leaves no trace: the tree answers rc=$back_rc (its own verdict was rc=$base_rc) and names no probe"
fi

# Arm 2: the same harm in a scratch tree that carries the registry — one beat is
# all it takes for every runtime with no beat to be reported stale.
ids="$(grep -E '^[[:space:]]*-[[:space:]]*id:[[:space:]]*' fleet/runtimes.yaml | sed -E 's/^[[:space:]]*-[[:space:]]*id:[[:space:]]*//; s/[[:space:]]+$//')"
first="$(printf '%s\n' "$ids" | head -1)"
second="$(printf '%s\n' "$ids" | tail -1)"
if [ -z "$first" ] || [ "$first" = "$second" ]; then
  echo "check-runtime-liveness: CANNOT-ASSESS — fleet/runtimes.yaml does not declare two runtimes to reason about" >&2
  exit 2
fi
scratch="/tmp/runtime-liveness-leak-control.$$.$(date +%s)"
if ! mkdir -p "$scratch/fleet" "$scratch/.fleet/runtime-beats" 2>/dev/null; then
  echo "check-runtime-liveness: CANNOT-ASSESS — cannot create $scratch" >&2
  exit 2
fi
cp -a fleet/runtimes.yaml "$scratch/fleet/runtimes.yaml" || exit 2
printf '{"commit":"liveness-leak-control","runtime":"%s","state":"running","ts":%s}\n' \
  "$first" "$(date +%s)" > "$scratch/.fleet/runtime-beats/$first.json" || exit 2
scratch_out="$(judge_at "$scratch" 2>&1)"
scratch_rc=$?
if [ "$scratch_rc" -eq 1 ] && [[ "$scratch_out" == *"runtime-stale:$second"* ]]; then
  echo "  OK    one un-earned beat makes every runtime with no beat stale ($second) — the chain #1459 measured"
else
  echo "  FAIL  the chain did not reproduce in a scratch tree: rc=$scratch_rc for $first beating and $second silent" >&2
  printf '%s\n' "$scratch_out" | sed 's/^/        /' >&2
  fail=$((fail + 1))
fi
rm -f "$scratch/.fleet/runtime-beats/$first.json"
scratch_out="$(judge_at "$scratch" 2>&1)"
scratch_rc=$?
if [ "$scratch_rc" -eq 0 ]; then
  echo "  OK    remove that one beat and the same tree is inert again (rc 0) — the harm was the un-earned beat"
else
  echo "  FAIL  the scratch tree still answers rc=$scratch_rc with no beat in it at all" >&2
  fail=$((fail + 1))
fi
rm -rf "$scratch"
scratch=""

# --- the judgement of this tree's own beats -----------------------------------
PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" python3 -m fleet.runtime_liveness "$verb" --root "$root"
rc=$?

case "$rc" in
  0) echo "check-runtime-liveness: OK" ;;
  1) echo "check-runtime-liveness: NOT-OK" >&2 ;;
  2) echo "check-runtime-liveness: CANNOT-ASSESS" >&2 ;;
  *) echo "check-runtime-liveness: CANNOT-ASSESS — judge exited $rc" >&2; rc=2 ;;
esac

if [ "$fail" -gt 0 ]; then
  echo "check-runtime-liveness: NOT-OK — the leak control failed ($fail arm(s))" >&2
  if [ "$rc" -eq 0 ]; then rc=1; fi
fi
exit "$rc"
