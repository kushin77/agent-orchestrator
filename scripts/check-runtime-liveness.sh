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
# The REAL-TREE stage then names the HOST's own facts APART instead of collapsing
# them: a registered runtime that has never beaten here is `runtime-unbeaten:<id>`
# — a NAMED GAP, printed and never a red, because no producer is installed for it
# on this host — while a runtime whose beat EXISTS and has stopped stays
# `runtime-stale:<id>` and NOT-OK. `reframe_controls` provokes that split on every
# run, and the note above `never_beaten` records the measurement that required it.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. Findings are named
# `runtime-stale:<id>`, `runtime-drift:<id>`, `runtime-unregistered:<id>`;
# `runtime-unbeaten:<id>` names a gap, and never sets rc 1 on its own.
#
# Usage:
#   bash scripts/check-runtime-liveness.sh               # producers, then the real tree
#   bash scripts/check-runtime-liveness.sh --self-test    # negative controls
#   bash scripts/check-runtime-liveness.sh --producers    # the producers stage alone
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
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

contains() { # contains <haystack> <needle> — bash-native, so it cannot SIGPIPE a producer
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- the real tree: what THIS host can supply, named rather than collapsed ----
# The pure judge keeps its own rule: the FIRST beat engages it, and from then on
# every registered runtime without a beat is `runtime-stale:<id>`. That rule is
# right for a fleet whose producers are installed — `--self-test`'s
# FIRST-BEAT-ENGAGES-THE-JUDGE probe pins it, and this stage does not change it.
# It is the WRONG reading of a tree where no producer is installed, which is what
# a lane worktree cut from `master` always is: `.fleet/runtime-beats/` there is
# empty, so this stage has only ever reported `no-beats-yet`. The one beat that
# CAN appear in one is a FIXTURE ARTIFACT, not a runtime —
# `scripts/check-spawn-envelope.sh` drives `fleet/terminal.py::run_once` against
# the real tree, and `run_once` beats before it can refuse. That hazard is named
# in the producers' own docstrings ("one stray beat engages the judge ... and
# reports every OTHER registered runtime `runtime-stale`"; measured there as "red
# on five innocent runtimes"). Measured on the folded tree: with
# `.fleet/runtime-beats/deepseek-executor.json` on disk the judge named the other
# six and the run ended NOT-OK; the same tree with that one file removed reports
# `no-beats-yet` and rc 0. The red was the fixture's, not a runtime's.
#
# So the two facts are NAMED APART, never merged into one word:
#   * `runtime-unbeaten:<id>` — registered here, and NO beat has ever been
#     recorded for it, so no producer was ever installed to supply one. A NAMED
#     GAP: printed, and never a red, because this check cannot supply that
#     runtime's producer on this host — a worktree has no cron, and a beat this
#     stage faked would go stale inside the window and red again.
#   * `runtime-stale:<id>` — a beat EXISTS and is past the window: the runtime
#     BEAT and then STOPPED, which is the fact this gate is for. Still a red — as
#     are `runtime-drift:<id>` and `runtime-unregistered:<id>`.
# The producers stage is what keeps the split from being a weakening: it drives
# every registered runtime's OWN producer on a scratch fleet and requires seven
# FRESH beats and then `runtime-stale:deepseek-sister` BY NAME on every run, so
# "beat, then stopped" is proven red whatever this host's real tree holds.
#
# In `make verify` the artifact is also always FRESH where it is judged: the probe
# that writes it is `check-fleet-runner-preflight.sh` (an admitted spawn through
# `terminal.run_once`), and discovery puts that check BEFORE this one, so the beat
# cannot read as "beat, then stopped" inside a gate run. Run this check ALONE on a
# tree whose last preflight was over an hour ago and that one beat IS past the
# window — which is the honest verdict (b) deliberately keeps, and
# `reframe_controls`' `stopped-stays-red` arm proves it is not swallowed here.

# never_beaten <root> — the registered runtimes with no beat at all here: absent
# from the gate's own reader AND with no beat file on disk. A TORN file is a
# problem to report, not an absence to excuse, so it is deliberately NOT a gap.
# Both sets come from the judge's own readers, so this is the judge's
# "no beat has ever been recorded" set by construction. An unreadable registry
# prints nothing: the judge is already reporting CANNOT-ASSESS for it, and an
# empty gap set leaves this stage's judgments exactly as they were.
never_beaten() {
  env PYTHONPATH="$1" python3 - "$1" <<'PY' 2>/dev/null
import sys
from pathlib import Path

from fleet import runtimes
from fleet.runtime_liveness import beat_module_beats

root = Path(sys.argv[1]).resolve()
try:
    registered = runtimes.ids(root)
except Exception:  # noqa: BLE001 - a refusal is the judge's CANNOT-ASSESS, not this stage's
    raise SystemExit(0)

beats = beat_module_beats(root)
for runtime_id in sorted(registered):
    if runtime_id in beats:
        continue
    if (root / ".fleet" / "runtime-beats" / f"{runtime_id}.json").exists():
        continue
    print(runtime_id)
PY
}

# partition_gaps <gaps> <the judge's output> — reframe every finding that is a
# `runtime-stale` for a runtime in <gaps> as a named, non-failing gap, and leave
# everything else (any other code, any other runtime) in `unbeaten_strays`.
# Returns 0 only when EVERY finding the judge made named one of the gaps, so the
# set is reframed whole or not at all: a mixed result can never quietly drop the
# finding that matters.
unbeaten_gap_lines=""
unbeaten_strays=""
unbeaten_named=0
partition_gaps() {
  local gap_list gaps_total named=0 line name id
  gap_list=" $(printf '%s\n' "$1" | tr '\n' ' ') "
  gaps_total="$(printf '%s\n' "$1" | grep -c . || true)"
  unbeaten_gap_lines=""
  unbeaten_strays=""
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    name="${line#  }"
    name="${name%% *}"
    id="${name#*:}"
    case "$name" in
      runtime-stale:*)
        if contains "$gap_list" " $id "; then
          named=$((named + 1))
          unbeaten_gap_lines="${unbeaten_gap_lines}  runtime-unbeaten:${id} — no beat has ever been recorded for this runtime on this host (no producer is installed for it here)"$'\n'
          continue
        fi
        ;;
    esac
    unbeaten_strays="${unbeaten_strays}${line}"$'\n'
  done <<<"$2"
  unbeaten_named="$named"
  # Every finding reframed AND nothing left over. The second half is load-bearing:
  # a run that named both gaps and ALSO kept a stray has not reframed the set, and
  # returning 0 on the count alone would have swallowed the stray (measured by the
  # stopped-stays-red control when this read `[ "$named" -eq "$gaps_total" ]`).
  [ "$named" -eq "$gaps_total" ] && [ -z "$unbeaten_strays" ]
}

# --- the reframing's own controls --------------------------------------------
# `partition_gaps` is the one place on this gate where a red becomes an OK, so it
# is provoked on every `run` — the rule the producers stage already follows, and
# for the same reason: an arm nobody falsifies passes for the wrong reason.
reframe_controls() {
  local fail=0
  rf_ok() { printf '  OK    %-22s %s\n' "$1" "$2"; }
  rf_no() { printf '  FAIL  %-22s %s\n' "$1" "$2" >&2; fail=$((fail + 1)); }

  echo "== the never-beaten reframing: a gap is named, a stopped runtime is a red =="

  local gaps="claude-session"$'\n'"hermes"
  local all_gaps="  runtime-stale:claude-session — no beat has ever been recorded"$'\n'"  runtime-stale:hermes — no beat has ever been recorded"

  if partition_gaps "$gaps" "$all_gaps" &&
    [ "$unbeaten_named" -eq 2 ] &&
    contains "$unbeaten_gap_lines" "runtime-unbeaten:claude-session" &&
    contains "$unbeaten_gap_lines" "runtime-unbeaten:hermes" &&
    [ -z "$unbeaten_strays" ]; then
    rf_ok "never-beaten-are-gaps" "both never-beaten runtimes named as gaps, and no finding kept"
  else
    rf_no "never-beaten-are-gaps" "named=$unbeaten_named gaps=$unbeaten_gap_lines stray=$unbeaten_strays"
  fi

  local stopped="  runtime-stale:deepseek-executor — beat is 7200s old, past the 3600s window"
  if partition_gaps "$gaps" "$all_gaps"$'\n'"$stopped"; then
    rf_no "stopped-stays-red" "a recorded beat past the window was reframed as a gap"
  elif contains "$unbeaten_strays" "runtime-stale:deepseek-executor"; then
    rf_ok "stopped-stays-red" "a beat that stopped is kept as a red, beside the gaps"
  else
    rf_no "stopped-stays-red" "the stopped runtime was dropped: stray=$unbeaten_strays"
  fi

  local drifted="  runtime-drift:claude-session — running commit trails master by 9 (> 5) with no directive in flight"
  if partition_gaps "$gaps" "$drifted"; then
    rf_no "drift-is-not-a-gap" "drift against a never-beaten runtime was reframed as a gap"
  elif contains "$unbeaten_strays" "runtime-drift:claude-session"; then
    rf_ok "drift-is-not-a-gap" "the finding's code counts, not only the runtime id"
  else
    rf_no "drift-is-not-a-gap" "the drift finding was dropped: stray=$unbeaten_strays"
  fi

  local ghost="  runtime-unregistered:ghost — beat exists for an id fleet/runtimes.yaml does not declare"
  if partition_gaps "$gaps" "$ghost"; then
    rf_no "unregistered-not-a-gap" "an unregistered beat was reframed as a gap"
  elif contains "$unbeaten_strays" "runtime-unregistered:ghost"; then
    rf_ok "unregistered-not-a-gap" "an id outside the contract stays a red"
  else
    rf_no "unregistered-not-a-gap" "the unregistered finding was dropped: stray=$unbeaten_strays"
  fi

  if [ "$fail" -eq 0 ]; then
    echo "check-runtime-liveness: reframing OK — a never-beaten runtime is a named gap, and a stopped / drifted / unregistered one is not"
    return 0
  fi
  echo "check-runtime-liveness: reframing NOT-OK — $fail arm(s) failed" >&2
  return 1
}

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

if [ "$verb" = "run" ]; then
  # The reframing's own controls run FIRST: what they assert is exactly what the
  # judgment below relies on, so a broken split is refused before it is applied.
  reframe_controls || exit 1

  gaps="$(never_beaten "$root")"
  findings="$(judge "$root" "$verb" 2>&1)"
  rc=$?
  if [ "$rc" -eq 1 ] && [ -n "$gaps" ] && partition_gaps "$gaps" "$findings"; then
    printf '%s' "$unbeaten_gap_lines" >&2
    printf 'check-runtime-liveness: OK — %s registered runtime(s) have never beaten on this host, so no producer is installed for them here (named above); every recorded beat is inside the window\n' \
      "$unbeaten_named"
    exit 0
  fi
  # Anything else is the judge's own verdict, in its own words. The gaps are
  # reframed as a whole set or not at all, so a mixed result cannot drop the
  # finding that matters.
  if [ -n "$findings" ]; then printf '%s\n' "$findings"; fi
else
  judge "$root" "$verb"
  rc=$?
  if [ "$verb" = "self-test" ]; then
    reframe_controls || rc=1
  fi
fi

case "$rc" in
  0) echo "check-runtime-liveness: OK" ;;
  1) echo "check-runtime-liveness: NOT-OK" >&2 ;;
  2) echo "check-runtime-liveness: CANNOT-ASSESS" >&2 ;;
  *) echo "check-runtime-liveness: CANNOT-ASSESS — judge exited $rc" >&2; rc=2 ;;
esac
exit "$rc"
