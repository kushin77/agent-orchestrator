#!/usr/bin/env bash
# check-runtime-liveness.sh — the runtime-liveness gate (issue #1271, #1412).
#
# Every runtime beats through `integrations/paperclip/adapters/heartbeat`
# (`{runtime, commit, state, ts}`), registered in `fleet/runtimes.yaml`. This
# gate gathers the real inputs (beats under `.fleet/runtime-beats/`, git
# distance to `origin/master`, directives in flight under `.fleet/inbox/`) and
# hands them to the PURE judge in `fleet/runtime_liveness.py::judge`, which is what
# `--self-test` provokes directly.
#
# A GATE WITH NO INPUT IS INERT (issue #1412). #1376 landed the registry, the
# adapter and this judge, and NOTHING WROTE A BEAT: on the real tree the judge could
# only report `no-beats-yet`, so a runtime that had DIED could not be told apart
# from one that never reported at all. The PRODUCERS stage therefore runs before the
# real-tree judgment: it builds a scratch fleet on /tmp, drives EVERY registered
# runtime's own producer against it — the Claude hook (both rungs), the loop's own
# `beat_runtime` for the sister and the executor, the declared card line for
# `copilot-agent`, and the hermes/paperclip live-call producers — and requires the
# judge to see seven fresh beats. It then BACKDATES the sister's beat and requires
# `runtime-stale:deepseek-sister` BY NAME with the other six NOT named: an arm that
# only ever reds proves nothing, so the fresh stage is asserted first and the
# negative stage asserts the exact set.
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
#   bash scripts/check-runtime-liveness.sh               # producers, then the real tree
#   bash scripts/check-runtime-liveness.sh --self-test    # negative controls
#   bash scripts/check-runtime-liveness.sh --producers    # the producers stage alone
#
# ---knowledge---
# module_id: scripts.check-runtime-liveness
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, self-proving-gate, offline-hermetic]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#1271", "#1376", "#1412", "#1459"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-runtime-liveness: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

verb="run"
for arg in "$@"; do
  case "$arg" in
    --self-test) verb="self-test" ;;
    --producers) verb="producers" ;;
  esac
done


# judge <root> [extra args...] — the real judge, never a copy of it
judge() {
  local target="$1"
  local command="$2"
  shift 2
  env PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m fleet.runtime_liveness "$command" --root "$target" "$@"
}

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
  # #1459's section, on the generic reader above rather than a second copy of it:
  # the same `run` verb the real-tree judgement below uses, so the control and the
  # gate cannot drift apart.
  judge "$1" run
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
if [ "$provoked_rc" -eq 1 ] && contains "$provoked_out" "runtime-unregistered:$probe_id"; then
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
if contains "$back_out" "$probe_id"; then
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
if [ "$scratch_rc" -eq 1 ] && contains "$scratch_out" "runtime-stale:$second"; then
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

# --- the producers stage (#1412) ---------------------------------------------
# One scratch fleet, every runtime's REAL producer, then the judge. Built in a
# subshell whose EXIT trap removes it, so the cleanup fires once on every path out.
producers_stage() {
  (
    set -u
    fx="$(mktemp -d /tmp/ao-runtime-liveness.XXXXXX)" || {
      echo "check-runtime-liveness: CANNOT-ASSESS — no scratch directory for the producers stage" >&2
      exit 2
    }
    trap 'rm -rf "$fx"' EXIT

    fail=0
    ok() { printf '  OK    %-18s %s\n' "$1" "$2"; }
    no() { printf '  FAIL  %-18s %s\n' "$1" "$2" >&2; fail=$((fail + 1)); }

    # A fleet the judge can read: the contract, and a real repository, so every
    # producer records a commit git can actually name (`running_commit`).
    mkdir -p "$fx/fleet" || exit 2
    cp "$root/fleet/runtimes.yaml" "$fx/fleet/runtimes.yaml" || exit 2
    git -C "$fx" init -q 2>/dev/null
    git -C "$fx" -c user.name=producer-fixture -c user.email=producer@example.invalid \
      -c commit.gpgsign=false add -A >/dev/null 2>&1
    git -C "$fx" -c user.name=producer-fixture -c user.email=producer@example.invalid \
      -c commit.gpgsign=false commit -q -m "producer fixture" 2>/dev/null
    if [ -z "$(git -C "$fx" rev-parse HEAD 2>/dev/null)" ]; then
      echo "check-runtime-liveness: CANNOT-ASSESS — the scratch fleet has no commit to beat with" >&2
      exit 2
    fi

    echo "== the producers: every registered runtime posts a beat (#1412) =="

    # 1-2. the Claude rungs: the hook this repo PROVIDES, invoked exactly as the
    # one-line install in ~/.claude/settings.json invokes it.
    if bash "$root/fleet/hooks/claude-beat.sh" --root "$fx" --cwd "$fx" >/dev/null 2>&1; then
      ok "claude-session" "fleet/hooks/claude-beat.sh"
    else
      no "claude-session" "the hook did not write a beat"
    fi
    if bash "$root/fleet/hooks/claude-beat.sh" --runtime claude-subagent --root "$fx" --cwd "$fx" >/dev/null 2>&1; then
      ok "claude-subagent" "fleet/hooks/claude-beat.sh --runtime claude-subagent"
    else
      no "claude-subagent" "the hook did not write a beat for the subagent rung"
    fi

    # 3-4. the two DeepSeek rungs: the LOOP's own producer, driven through the same
    # module the loop imports (`fleet/terminal.py::beat_runtime`), fleet root
    # redirected to the scratch tree.
    for rung in deepseek-sister deepseek-executor; do
      out="$(env PYTHONPATH="$root/fleet:$root" python3 - "$fx" "$rung" <<'PY' 2>&1
import sys
from pathlib import Path

import beats
import terminal

terminal.ROOT = Path(sys.argv[1])
beats.ROOT = Path(sys.argv[1])
terminal.beat_runtime(sys.argv[2])
PY
)"
      rc=$?
      if [ "$rc" -eq 0 ] && [ -f "$fx/.fleet/runtime-beats/$rung.json" ]; then
        ok "$rung" "fleet/terminal.py::beat_runtime (the loop's producer)"
      else
        no "$rung" "the loop's producer did not write a beat (rc=$rc) ${out}"
      fi
    done

    # 5. copilot-agent: the ONE-LINE card command the SME profile cards declare,
    # run exactly as a card runs it (`fleet/beats.py` is the producer).
    if env PYTHONPATH="$root" python3 -m fleet.beats post --runtime copilot-agent \
        --root "$fx" --cwd "$fx" >/dev/null 2>&1; then
      ok "copilot-agent" "python3 -m fleet.beats post --runtime copilot-agent (the card line)"
    else
      no "copilot-agent" "the declared card line did not write a beat"
    fi

    # 6. hermes: the adapter's OWN live-call path, driven offline — the producer
    # `cmd_probe` calls once the service has answered.
    if env PYTHONPATH="$root" python3 - "$fx" <<'PY' >/dev/null 2>&1
import sys
from pathlib import Path

from integrations.hermes import cli

cli._beat_live_call(Path(sys.argv[1]))
PY
    then
      ok "hermes" "integrations/hermes/cli.py::_beat_live_call (the probe verb)"
    else
      no "hermes" "the hermes live-call producer did not write a beat"
    fi

    # 7. paperclip: the same, for the adapter's live dependency read (`health`).
    if env PYTHONPATH="$root" python3 - "$fx" <<'PY' >/dev/null 2>&1
import sys
from pathlib import Path

from integrations.paperclip.api import cli as api_cli
from integrations.paperclip.api import health as health_mod

api_cli._beat_live_read(
    Path(sys.argv[1]),
    health_mod.HealthReport(status=health_mod.STATUS_OK, http_status=200, dependencies=()),
)
PY
    then
      ok "paperclip" "integrations/paperclip/api/cli.py::_beat_live_read (the health verb)"
    else
      no "paperclip" "the paperclip live-read producer did not write a beat"
    fi

    echo "== the judge over those beats: every registered runtime is live =="
    out="$(judge "$fx" run 2>&1)"
    rc=$?
    beats="$(ls "$fx/.fleet/runtime-beats" 2>/dev/null | wc -l)"
    if [ "$rc" -eq 0 ] && contains "$out" "every registered runtime is live"; then
      ok "fresh-beats" "$(printf '%s' "$out" | tail -1) [beats=$beats]"
    else
      no "fresh-beats" "rc=$rc and the judge did not report every runtime live: $(printf '%s' "$out" | tail -3)"
    fi

    # The control: the sister goes quiet. Its beat is backdated past the declared
    # window — a producer that stopped beating — and the judge must name it AND
    # ONLY it: a stage that named every runtime would pass this arm for the wrong
    # reason.
    echo "== the control: the sister stops beating =="
    backdate_out="$(env PYTHONPATH="$root" python3 - "$fx/.fleet/runtime-beats/deepseek-sister.json" <<'PY' 2>&1
import json
import sys
import time
from pathlib import Path

path = Path(sys.argv[1])
record = json.loads(path.read_text(encoding="utf-8"))
record["ts"] = time.time() - 7200.0
path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("backdated %s" % record["runtime"])
PY
)"
    if ! contains "$backdate_out" "backdated deepseek-sister"; then
      no "control-setup" "the backdate did not land: $backdate_out"
    fi
    stale="$(judge "$fx" run 2>&1)"
    stale_rc=$?
    if [ "$stale_rc" -eq 1 ] && contains "$stale" "runtime-stale:deepseek-sister"; then
      ok "stale-by-name" "$(printf '%s' "$stale" | grep 'runtime-stale' | head -1)"
    else
      no "stale-by-name" "expected rc=1 naming runtime-stale:deepseek-sister, got rc=$stale_rc: $stale"
    fi
    for other in claude-session claude-subagent deepseek-executor copilot-agent hermes paperclip; do
      if contains "$stale" "runtime-stale:$other"; then
        no "not-stale-$other" "the control named $other, which is still beating (the arm would pass vacuously)"
      fi
    done

    if [ "$fail" -eq 0 ]; then
      echo "check-runtime-liveness: producers OK — 7 runtimes beat through their own producers, and a runtime that stops beating is named"
      exit 0
    fi
    echo "check-runtime-liveness: producers NOT-OK — $fail arm(s) failed" >&2
    exit 1
  )
}

if [ "$verb" = "producers" ] || [ "$verb" = "run" ]; then
  producers_stage
  producers_rc=$?
  if [ "$producers_rc" -ne 0 ]; then
    if [ "$verb" = "producers" ]; then
      exit "$producers_rc"
    fi
    echo "check-runtime-liveness: NOT-OK — the producers stage failed, so no runtime can be judged" >&2
    exit 1
  fi
  if [ "$verb" = "producers" ]; then
    exit 0
  fi
fi

judge "$root" "$verb"
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
