#!/usr/bin/env bash
# check-fleet-runner-preflight.sh — the runner preflight gate (issue #733).
#
# MEASURED 2026-09-14: the fleet could not spawn a single subagent. `claude` was
# installed at `~/.local/bin/claude`, but the loop is cron's child and inherited
# cron's minimal PATH, so `subprocess` raised `FileNotFoundError: 'claude'` — and
# the failure repeated **per directive, per cycle**, escalating `critical` every
# time (a runaway amplifier) while four lanes with committed work could not land.
#
# This gate makes each half of the fix a NAMED failure, and it cannot pass by
# declaring things:
#
#   * `fleet/terminal.py` resolves its runner in code (`resolve_runner`), reports
#     it before the loop reads its inbox (`preflight`), holds the queue when it
#     cannot (`hold_queue_for_runner` / `release_runner_hold`), and treats a gate
#     it could not assess as CANNOT-ASSESS (`escalation_severity`) — each asserted
#     by name, and the preflight call site is asserted to PRECEDE the inbox read;
#   * `fleet/runtime.py` owns the search path (PATH first, then the HOME-derived
#     install directories) and `fleet/watchdog.py` passes it explicitly at spawn,
#     so the cron -> watchdog -> launcher -> loop -> subagent chain does not depend
#     on an inherited PATH;
#   * BEHAVIOUR: three queued directives are driven through the REAL loop in a
#     scratch tree with a PATH and HOME that cannot see the runner. The loop must
#     escalate exactly ONCE, dispatch NOTHING, leave the queue held — for a single
#     cycle and across many cycles (the storm signature), and it must not consume
#     the held work in the single-cycle case;
#   * BEHAVIOUR, the other half: a runner that is on NEITHER PATH nor the system
#     directories, but IS in the per-user install directory, is resolved and
#     actually EXECUTED (`run_once` spawns it and the child records its own argv),
#     which is the failure #733 measured, inverted;
#   * MUTATION: two mutants of the real loop are driven through the same scenario —
#     one with the preflight neutralised, one with the hold removed — and the gate
#     must FAIL for each. The mutation is asserted to have actually applied, and
#     the mutant runs in an isolated tree so nothing it does can touch this repo.
#   * ISOLATION (#1459): the probe in section 3b drives the REAL spawn path, which
#     posts a runtime beat (#1412) BEFORE the loop refuses or spawns — and
#     `beats.ROOT` is a module constant pointing at the checkout the producer was
#     imported from. Un-redirected, that beat lands in THIS repository, where
#     `scripts/check-runtime-liveness.sh` reads it and reports every OTHER
#     registered runtime `runtime-stale`: the composite's verdict would depend on
#     which check ran first. The probe is pointed at a tree this check owns, and
#     the repository's own beats are snapshotted before and after, so a leak is a
#     NAMED failure here instead of a mystery red in another check.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-fleet-runner-preflight.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

terminal="fleet/terminal.py"
watchdog="fleet/watchdog.py"
runtime_py="fleet/runtime.py"
verify="scripts/verify.sh"
readme="fleet/README.md"
suites="fleet/tests/test_runner_preflight.py"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-fleet-runner-preflight: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

for required in "$terminal" "$watchdog" "$runtime_py" "$verify" "$readme" "$suites"; do
  if [ ! -f "$required" ]; then
    echo "check-fleet-runner-preflight: FAIL — $required is missing" >&2
    exit 1
  fi
done

# An unresolvable runner name for the negative controls. It must not exist
# anywhere on the machine, or the control would be vacuous.
absent_runner="claude-733-absent"
if command -v "$absent_runner" >/dev/null 2>&1; then
  echo "check-fleet-runner-preflight: CANNOT-ASSESS — '$absent_runner' resolves on this box" >&2
  exit 2
fi

# A scratch dir WITHOUT a trailing run of `X`: this repo's docs-lint scans for
# unfinished markers and a mktemp X-suffix trips it, so this follows the repo's
# convention — an explicit /tmp name, with mkdir refusing loudly rather than
# silently reusing another run's tree.
work="/tmp/ao733-runner-gate.$(date +%s%N).$$"
if ! mkdir "$work" 2>/dev/null; then
  echo "check-fleet-runner-preflight: CANNOT-ASSESS — cannot create scratch dir $work" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

# The runtime-beat producer (#1412) posts a beat — `<beats.ROOT>/.fleet/runtime-beats/
# <id>.json` — BEFORE the loop refuses or spawns, and `beats.ROOT` is a module
# constant pointing at the checkout the producer was imported from. The probe in
# section 3b drives `run_once` for real IN THIS REPOSITORY, so un-redirected it
# stamps a beat here — where `scripts/check-runtime-liveness.sh` reads it and
# reports every OTHER registered runtime `runtime-stale` (#1459). Every probe
# below is pointed at a tree THIS CHECK owns instead, exactly as
# `fleet/tests/conftest.py` points every test: the tree carries the registry the
# producer checks its id against, so the beat really is written, just not into
# the repository.
beats_tree="$work/beats-tree"
if ! mkdir -p "$beats_tree/fleet" "$beats_tree/.fleet/runtime-beats" 2>/dev/null; then
  echo "check-fleet-runner-preflight: CANNOT-ASSESS — cannot create $beats_tree" >&2
  exit 2
fi
if [ -f fleet/runtimes.yaml ]; then
  cp -a fleet/runtimes.yaml "$beats_tree/fleet/runtimes.yaml" || exit 2
fi

beats_files() { # beats_files <tree> — each beat file with the size+mtime a rewrite would move
  if [ -d "$1/.fleet/runtime-beats" ]; then
    find "$1/.fleet/runtime-beats" -maxdepth 1 -type f -name '*.json' -printf '%f %s %T@\n' 2>/dev/null | sort
  fi
}

# The repository's own beats BEFORE this check runs anything: compared again at
# the end, so a leaked beat is named here rather than surfacing as an
# order-dependent `runtime-stale` in another check.
repo_beats_before="$(beats_files "$root")"

fail=0
note_fail() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail + 1)); }

contains() { # contains <haystack> <needle> — bash-native, so it cannot SIGPIPE a producer
  case "$1" in
    *"$2"*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- 1. the declarations -----------------------------------------------------
# Each name is the mechanism, not the wording: removing it fails this gate.

declare -a declarations=(
  "$terminal|def resolve_runner(|def preflight(|def hold_queue_for_runner(|def release_runner_hold(|def escalation_severity(|GATE_CANNOT_ASSESS = \"CANNOT-ASSESS\"|RUNNER_PREFLIGHT_ID = |timed out after {timeout}s"
  "$runtime_py|RUNNER_DIRS = |def runner_search_path(|def runner_env(|~/.local/bin"
  "$watchdog|env=runtime.runner_env()"
  "$verify|check-fleet-runner-preflight.sh"
  "$readme|preflight|FLEET_RUNNER|resolve_runner"
)

missing_declaration() { # missing_declaration <file> <marker...>
  local file="$1" marker missing=0
  shift
  for marker in "$@"; do
    if ! grep -qF -- "$marker" "$file"; then
      printf '  FAIL  %s (missing: %s)\n' "$file" "$marker" >&2
      missing=1
    fi
  done
  return "$missing"
}

for entry in "${declarations[@]}"; do
  IFS='|' read -r -a parts <<< "$entry"
  if missing_declaration "${parts[0]}" "${parts[@]:1}"; then
    echo "  OK    ${parts[0]} declares the runner-preflight contract"
  else
    fail=$((fail + 1))
  fi
done

# --- 2. the preflight really precedes the inbox read -------------------------
# "Before the loop reads the inbox" is a position, not a claim about the code.
preflight_line="$(grep -n 'runner_ok, runner_detail = preflight(args.runner)' "$terminal" | sed -n '2p' | cut -d: -f1)"
watch_line="$(grep -n 'watch_command = \["python3", CHANNEL, "watch"' "$terminal" | head -1 | cut -d: -f1)"
if [ -z "$preflight_line" ] || [ -z "$watch_line" ]; then
  note_fail "could not locate the in-loop preflight call ($preflight_line) or the inbox read ($watch_line)"
elif [ "$preflight_line" -lt "$watch_line" ]; then
  echo "  OK    the preflight (line $preflight_line) precedes the inbox read (line $watch_line)"
else
  note_fail "the preflight (line $preflight_line) does NOT precede the inbox read (line $watch_line)"
fi

# --- 3. the behavioural scenario, on the REAL loop ---------------------------
#
# A scratch tree holding only what the loop imports, so the run cannot reach this
# repository's board, worktrees or claims. The runner is absent from PATH *and*
# from HOME, which is where a per-user install would be found.
build_tree() { # build_tree <dir>
  local tree="$1"
  mkdir -p "$tree/fleet" "$tree/governance" || return 1
  cp -a fleet/*.py "$tree/fleet/" || return 1
  cp -a fleet/schema "$tree/fleet/" || return 1
  # The whole governance package is required to import the loop: `governance` is a
  # namespace package whose `reconcile` package pulls in `lifecycle`, which pulls
  # in more — the closure is deep, so it is copied rather than guessed. `fleet/
  # profiles/` is deliberately NOT copied: a mutant that gets past the preflight
  # then fails the FinOps resolution by name, so no scenario here can reach the
  # board, the worktrees or the network.
  cp -a governance/. "$tree/governance/" || return 1
  find "$tree/governance" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
  find "$tree/governance" -name tests -type d -prune -exec rm -rf {} + 2>/dev/null
  return 0
}

queue_directives() { # queue_directives <tree> <fleetdir>
  local tree="$1" fleetdir="$2" index
  mkdir -p "$work/messages" || return 1
  for index in 1 2 3; do
    # Issue numbers that cannot exist on a board, so even a rogue dispatch cannot
    # claim real work.
    cat > "$work/messages/$index.json" <<JSON
{
  "type": "directive",
  "from": "brain",
  "to": "sister",
  "ts": "2026-09-14T00:00:0${index}Z",
  "task": {"kind": "work", "issue": 90000${index}, "lane": "fleet"},
  "body": "runner-preflight gate fixture ${index}"
}
JSON
    env -C "$tree" \
      AO_FLEET_DIR="$fleetdir" \
      PATH=/usr/bin:/bin \
      HOME="$work/home" \
      python3 fleet/channel.py send --message "$work/messages/$index.json" >/dev/null 2>&1 || return 1
  done
}

count_type() { # count_type <fleetdir> <type>
  python3 - "$1" "$2" <<'PY'
import json
import pathlib
import sys

outbox = pathlib.Path(sys.argv[1]) / "outbox"
wanted = sys.argv[2]
total = 0
if outbox.is_dir():
    for path in sorted(outbox.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("type") == wanted:
            total += 1
print(total)
PY
}

named_runner() { # named_runner <fleetdir> <runner>  -> 1 when an escalation names it
  python3 - "$1" "$2" <<'PY'
import json
import pathlib
import sys

outbox = pathlib.Path(sys.argv[1]) / "outbox"
runner = sys.argv[2]
named = 0
if outbox.is_dir():
    for path in sorted(outbox.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("type") == "escalate" and runner in str(record.get("body", "")):
            named = 1
print(named)
PY
}

count_files() { # count_files <dir> <glob>
  local found
  found="$(find "$1" -maxdepth 1 -name "$2" -type f 2>/dev/null | wc -l)"
  printf '%s' "$found"
}

observe() { # observe <label> <tree> <fleetdir> <once|loop>
  local label="$1" tree="$2" fleetdir="$3" mode="$4"
  local -a extra=()
  local rc=0 pid started=0
  [ "$mode" = "once" ] && extra+=(--once)
  if [ "$mode" = "loop" ]; then
    # Bounded, many-cycle run: the storm signature needs more than one cycle.
    env -C "$tree" \
      AO_FLEET_DIR="$fleetdir" PATH=/usr/bin:/bin HOME="$work/home" \
      PYTHONDONTWRITEBYTECODE=1 \
      python3 fleet/terminal.py run --runner "$absent_runner" --watch-timeout 0.1 --idle-sleep 0.1 \
      > "$work/$label.log" 2>&1 &
    pid=$!
    sleep 2
    kill -TERM "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    rc=$?
    started=1
  else
    env -C "$tree" \
      AO_FLEET_DIR="$fleetdir" PATH=/usr/bin:/bin HOME="$work/home" \
      PYTHONDONTWRITEBYTECODE=1 \
      python3 fleet/terminal.py run --runner "$absent_runner" --watch-timeout 0.1 --idle-sleep 0.1 \
      "${extra[@]}" > "$work/$label.log" 2>&1
    rc=$?
  fi

  local escalates pending runs held named
  escalates="$(count_type "$fleetdir" escalate)"
  pending="$(count_files "$fleetdir/inbox" '*.json')"
  runs="$(count_files "$fleetdir/runs" '*.json')"
  held=0; [ -e "$fleetdir/paused" ] && held=1
  named="$(named_runner "$fleetdir" "$absent_runner")"
  # `started` distinguishes "we stopped it" from "it stopped itself"; the mode
  # decides which invariants apply to this row.
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$label" "$mode" "$rc" "$escalates" "$pending" "$runs" "$held" "$named" "$started" >> "$observations"
}

observations="$work/observations.tsv"
: > "$observations"

if ! build_tree "$work/tree"; then
  echo "check-fleet-runner-preflight: CANNOT-ASSESS — could not build the scratch tree" >&2
  exit 2
fi
mkdir -p "$work/home" "$work/fleet-a" "$work/fleet-b"
if ! queue_directives "$work/tree" "$work/fleet-a" || ! queue_directives "$work/tree" "$work/fleet-b"; then
  echo "check-fleet-runner-preflight: CANNOT-ASSESS — could not queue the fixture directives" >&2
  exit 2
fi
queued="$(count_files "$work/fleet-a/inbox" '*.json')"
if [ "$queued" -ne 3 ]; then
  echo "check-fleet-runner-preflight: CANNOT-ASSESS — queued $queued directives, expected 3" >&2
  exit 2
fi

  observe "once" "$work/tree" "$work/fleet-a" once
  observe "loop" "$work/tree" "$work/fleet-b" loop

# --- 3b. the spawn case: a runner off PATH and in the user's own bin really runs ---
#
# The issue's first acceptance criterion, end to end: a runner that is on NEITHER
# PATH nor the default system directories, but IS in the per-user install
# directory a real install uses, must be resolved and SPAWNED. The stub records
# its own $0 and its argv, so the assertion is "a child really ran, and it was the
# resolved path" — not "the code says it would".
spawn_case() { # spawn_case <home> <marker> <log>
  local home="$1" marker="$2" log="$3"
  mkdir -p "$home/.local/bin" || return 1
  cat > "$home/.local/bin/claude-733-probe" <<STUB
#!/bin/sh
printf '%s\n' "\$0 \$*" > "$marker"
exit 0
STUB
  chmod +x "$home/.local/bin/claude-733-probe" || return 1
  env -C "$root" HOME="$home" PATH=/usr/bin:/bin AO_FLEET_DIR="$work/fleet-spawn" \
    PYTHONDONTWRITEBYTECODE=1 python3 - "$beats_tree" > "$log" 2>&1 <<'PY'
import os
import sys
from pathlib import Path

sys.path.insert(0, "fleet")
sys.path.insert(0, ".")
import terminal  # noqa: E402
from governance.spawn import sources  # noqa: E402

# The beat this probe's spawn path posts (#1412) belongs to THIS CHECK, not to the
# repository `run_once` was imported from: `beats.ROOT` is a module constant, and
# a beat left in the repository engages `scripts/check-runtime-liveness.sh` and
# reports every other registered runtime `runtime-stale` (#1459). Same
# redirection `fleet/tests/conftest.py` gives every test.
try:
    import beats  # noqa: E402
    beats.ROOT = Path(sys.argv[1]).resolve()
except ImportError:  # the producer (#1412) is not in this tree: nothing to redirect
    pass

# The spawn envelope is a PRECONDITION (#793): a probe that drives the run path
# supplies one, so this check keeps measuring the RUNNER (resolve it off PATH,
# spawn it, record its argv) instead of measuring the refusal. The sources are
# injected rather than read, so the probe stays offline and does not depend on
# this box's board, claim ledger or gate permits.
_WORKTREE = os.getcwd()
_AGENT = "subagent-733"
sources.collect = lambda **_: {
    "issue": 900001,
    "lane": "fleet",
    "worktree": _WORKTREE,
    "session": {
        "id": "checkrunnerpreflight01",
        "issue": "900001",
        "agent": _AGENT,
        "lane": "fleet",
        "branch": "issue-900001",
        "worktree": _WORKTREE,
        "repo_slug": "kushin77/agent-orchestrator",
        "author_name": f"agent-{_AGENT}",
        "author_email": f"agent+{_AGENT}@agents.invalid",
        "committer_name": f"agent-{_AGENT}",
        "committer_email": f"agent+{_AGENT}@agents.invalid",
    },
    "trailer": "Refs kushin77/agent-orchestrator#900001",
    "claim": {"owner": _AGENT, "state": "claim", "lane": "fleet", "at": "2026-09-15T00:00:00Z"},
    "focus": {"epic": 160, "source": "pinned-focus", "pinned_epic": 160, "wave_cap": 12, "max_agents": 0},
    "capacity": {
        "effective": 1,
        "binding": "disjoint",
        "assessed": True,
        "bounds": [],
        "problems": [],
        "permit": {
            "store": "/tmp/check-fleet-runner-preflight-gates",
            "worktree_key": "checkrunnerpreflight",
            "lock": "/tmp/check-fleet-runner-preflight-gates/checkrunnerpreflight.lock",
            "max_concurrent": 4,
        },
    },
    "budget": {"attempts": 0, "cap": 5, "state": "pending", "next_attempt_at": None},
    "gate": {
        "of_record": "make verify",
        "bound": "at most one composite gate per worktree, bounded box-wide (AO-GR-22)",
        "entry": "scripts/gate-lock.sh",
        "max_concurrent": 4,
        "ttl_seconds": 900,
    },
    "verify": {"command": "make verify", "source": "gate-of-record"},
    "spawn": {"path": "fleet", "agent": _AGENT, "directive": "d-733-spawn"},
}

rc, output = terminal.run_once(
    {"id": "d-733-spawn", "task": {"kind": "work", "issue": 900001, "lane": "fleet"}},
    "claude-733-probe -p",
    10.0,
    False,
    "subagent-733",
)
print(f"run_once rc={rc} output={output!r}")
raise SystemExit(0 if rc == 0 else 1)
PY
}

spawn_home="$work/home-spawn"
spawn_marker="$work/spawned.txt"
spawn_log="$work/spawn.log"
if spawn_case "$spawn_home" "$spawn_marker" "$spawn_log" \
   && grep -qF "$spawn_home/.local/bin/claude-733-probe" "$spawn_marker" \
   && grep -qF -- "--model" "$spawn_marker"; then
  echo "  OK    [spawn] a runner on neither PATH nor the system dirs was resolved and EXECUTED"
  echo "        spawned argv (truncated): $(head -c 120 "$spawn_marker")"
else
  note_fail "[spawn] the off-PATH runner was not executed: $(cat "$spawn_log" 2>/dev/null | tail -3) $(cat "$spawn_marker" 2>/dev/null)"
fi

# --- 3c. the beat the spawn path posts lands in THIS CHECK's tree -------------
#
# The redirect above is only honest if the producer still RUNS: a probe that
# avoided the beat by avoiding the spawn path would prove nothing. So the beat
# must be present, in the tree this check owns.
producer_beats="$(beats_files "$beats_tree")"
if [ -f fleet/beats.py ]; then
  if contains "$producer_beats" "deepseek-executor.json "; then
    echo "  OK    [beats] the spawn path posted its runtime beat (#1412) into this check's own tree"
  else
    note_fail "[beats] the spawn path's runtime beat is not in $beats_tree/.fleet/runtime-beats/ — the redirect is not reaching the producer"
  fi
else
  echo "  OK    [beats] #1412's producer is not in this tree, so no probe here can post a beat"
fi

# --- 4. the mutation proof ---------------------------------------------------
#
# Two mutants of the REAL loop, each removing one mechanism:
#   M1 the preflight is neutralised (`runner_ok` forced True) — the loop no longer
#      refuses, so it never names the runner and never holds the queue;
#   M2 the hold is removed (`if False:` instead of calling the hold) — the runner
#      is still reported, but the queue is not held.
# Each must be DETECTED by the invariants the positive cases assert, and the
# mutation is asserted to have actually applied before it is trusted.
mutate() { # mutate <src-tree> <dest-tree> <sed-expression> <proof-grep>
  local src="$1" dest="$2" expression="$3" proof="$4"
  cp -a "$src" "$dest" || return 1
  sed -i "$expression" "$dest/fleet/terminal.py" || return 1
  rm -rf "$dest"/fleet/__pycache__
  if ! grep -qF -- "$proof" "$dest/fleet/terminal.py"; then
    return 1
  fi
  # The mutant must differ from the original, or the control is vacuous.
  if cmp -s "$src/fleet/terminal.py" "$dest/fleet/terminal.py"; then
    return 1
  fi
  return 0
}

mutant_case() { # mutant_case <label> <expression> <proof>
  local label="$1" expression="$2" proof="$3"
  local tree="$work/tree-$label" fleetdir="$work/fleet-$label"
  mkdir -p "$fleetdir" || return 1
  if ! mutate "$work/tree" "$tree" "$expression" "$proof"; then
    note_fail "$label: the mutation did not apply (or the tree was unchanged)"
    return 1
  fi
  if ! queue_directives "$tree" "$fleetdir"; then
    note_fail "$label: could not queue the fixture directives"
    return 1
  fi
  echo "  OK    $label: the mutant applied ($(grep -cF -- "$proof" "$tree/fleet/terminal.py") proof line(s))"
  observe "$label" "$tree" "$fleetdir" once
}

# The runner gate is TWO ORDERED checks since #841 — does the runner RESOLVE, and
# can it HONOUR the model (`fleet/runners.py`) — and either one alone holds the queue
# by design. This mutant therefore removes the MECHANISM (both halves) rather than one
# half: a mutant that removes one half leaves the invariant intact, which is defence in
# depth working as intended, and demanding that it break would be asserting an
# implementation detail instead of the invariant. Removing both is the regression a
# future change could actually make, and the invariant must catch that one.
#
# The proof proves the #841 half landed; the preflight half proves itself — if THAT
# substitution had not applied, `runner_ok` would still be False and the queue would
# still be held, so this mutant would go undetected and fail the gate.
mutant_case "mutant-preflight" \
  's|^        runner_ok, runner_detail = preflight(args.runner)$|        runner_ok, runner_detail = True, ""|;s|^        capability_problem = runners.unhonourable(args.runner) if runner_ok else ""$|        capability_problem = ""|' \
  'capability_problem = ""'
mutant_case "mutant-hold" \
  's|^            if hold_queue_for_runner(runner_detail):$|            if False:  # mutant: no hold|' \
  'if False:  # mutant: no hold'

# --- 5. the verdict ----------------------------------------------------------
# Every invariant is asserted by name; a mutant is DETECTED when at least one of
# them fails. The whole judgement is one place, so the positive and negative cases
# cannot drift into different standards.
if ! python3 - "$observations" "$absent_runner" <<'PY'
import sys

rows = []
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        line = line.rstrip("\n")
        if not line:
            continue
        label, mode, rc, escalates, pending, runs, held, named, started = line.split("\t")
        rows.append(
            {
                "label": label,
                "mode": mode,
                "rc": int(rc),
                "escalates": int(escalates),
                "pending": int(pending),
                "runs": int(runs),
                "held": int(held),
                "named": int(named),
                "started": int(started),
            }
        )
runner = sys.argv[2]
positive = [row for row in rows if not row["label"].startswith("mutant-")]
mutants = [row for row in rows if row["label"].startswith("mutant-")]
problems = []


def positive_invariants(row):
    """The named invariants a loop without a working preflight must violate."""
    checks = [
        ("one-escalation", row["escalates"] == 1, f"{row['escalates']} escalation(s), expected 1"),
        ("escalation-names-the-runner", row["named"] == 1, f"no escalation named {runner!r}"),
        ("hold-the-queue", row["held"] == 1, "the queue was not held (.fleet/paused absent)"),
        ("dispatch-nothing", row["runs"] == 0, f"{row['runs']} run marker(s): something was dispatched"),
    ]
    if row["mode"] == "once":
        # The single-cycle case exits by itself, so its exit code is a verdict; the
        # work must also still be queued, untouched.
        checks.append(("report-the-failure", row["rc"] == 1, f"exit code {row['rc']}, expected 1 (NOT-OK)"))
        checks.append(
            ("leave-the-work-queued", row["pending"] == 3, f"{row['pending']} directive(s) left pending, expected 3")
        )
    else:
        # The many-cycle case is stopped by THIS gate, so 143 is the proof that it
        # really ran until we killed it.
        checks.append(("stopped-by-the-gate", row["rc"] == 143, f"exit code {row['rc']}, expected 143 (SIGTERM)"))
    return checks


#: Which invariant each mutant must break: the mechanism it removed, not merely
#: "something somewhere failed". A mutant that trips only an unrelated invariant is
#: not evidence for the mechanism it claims to remove.
mutant_mechanism = {
    "mutant-preflight": "hold-the-queue",
    "mutant-hold": "hold-the-queue",
}


for row in positive:
    for name, ok, detail in positive_invariants(row):
        if ok:
            print(f"  OK    [{row['label']}] {name}")
        else:
            print(f"  FAIL  [{row['label']}] {name} — {detail}", file=sys.stderr)
            problems.append(f"{row['label']}:{name}")

if len(mutants) != 2:
    print(f"  FAIL  expected 2 mutants, measured {len(mutants)}", file=sys.stderr)
    problems.append("mutation-cover")
for row in mutants:
    broken = [name for name, ok, _ in positive_invariants(row) if not ok]
    required = mutant_mechanism.get(row["label"])
    if broken and (required is None or required in broken):
        print(f"  OK    [{row['label']}] detected — invariant(s) broken: {', '.join(broken)}")
    else:
        print(
            f"  FAIL  [{row['label']}] NOT detected: the mutant kept every invariant "
            f"(escalates={row['escalates']} runs={row['runs']} held={row['held']}, "
            f"broke={broken or 'nothing'}, required={required})",
            file=sys.stderr,
        )
        problems.append(f"{row['label']}:undetected")

raise SystemExit(1 if problems else 0)
PY
then
  fail=$((fail + 1))
fi

# --- 5. the isolation the redirect buys: the repository is untouched ---------
repo_beats_after="$(beats_files "$root")"
if [ "$repo_beats_before" = "$repo_beats_after" ]; then
  echo "  OK    [beats] this check left no runtime beat in the repository"
else
  note_fail "[beats] this check posted a runtime beat into $root/.fleet/runtime-beats/ (before=[$(printf '%s' "$repo_beats_before" | tr '\n' ' ')] after=[$(printf '%s' "$repo_beats_after" | tr '\n' ' ')]) — a stray beat engages scripts/check-runtime-liveness.sh, which then reports every OTHER registered runtime runtime-stale, so the composite's verdict depends on which check ran first (#1459)"
fi

if [ "$fail" -gt 0 ]; then
  echo "check-fleet-runner-preflight: FAIL ($fail violation(s))" >&2
  exit 1
fi
echo "check-fleet-runner-preflight: OK — the runner is resolved in code, one escalation holds the queue, both mutants are detected, and the spawn path's runtime beat landed in this check's own tree instead of the repository"
exit 0
