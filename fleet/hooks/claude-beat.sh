#!/usr/bin/env bash
# claude-beat.sh — the Claude sessions' runtime-beat producer (issue #1412).
#
# THE GAP THIS CLOSES
#   #1376 landed the runtime registry (`fleet/runtimes.yaml`), the beat adapter
#   (`.fleet/runtime-beats/<id>.json`) and the liveness judge
#   (`scripts/check-runtime-liveness.sh`). Nothing WROTE a beat, so on the real
#   tree the judge could only report `no-beats-yet`: a gate whose input is never
#   produced is inert, and the runtime it claims to watch cannot go stale because
#   it never reported at all. This script is the producer for `claude-session`
#   and `claude-subagent` — the two runtimes no loop in this repo owns.
#
# WHAT A BEAT IS
#   `{runtime, commit, state, ts}` written atomically into
#   `<fleet root>/.fleet/runtime-beats/<runtime>.json` by
#   `fleet/beats.py` (which delegates the record itself to
#   `integrations/paperclip/adapters/heartbeat/beat.py`, the contract's writer).
#   The `commit` is the commit the SESSION is running — `git rev-parse HEAD` of
#   the session's project dir — because the judge's drift finding
#   (`runtime-drift:<id>`) compares that commit to `origin/master`. The beat
#   always lands in THIS script's fleet tree (the tree that owns the hook), never
#   in whatever worktree the session happens to be standing in, so there is one
#   place the judge reads.
#
# THE ONE-LINE INSTALL (never run by a lane, and never in this repo's tree)
#   `bash fleet/hooks/claude-beat.sh --print-install` prints ONE line: the value
#   of `"hooks"` in the owner's `~/.claude/settings.json`. Adding it is the
#   owner's step — the fleet PROVIDES the hook and documents it; it does not
#   edit the owner's live settings. `fleet/README.md` quotes the exact line the
#   script prints, and `fleet/tests/test_runtime_beats.py` asserts the two are
#   byte-identical, so the documented install cannot drift from the script.
#
# WHY IT IS CHEAP
#   A `SessionStart` hook fires once, but a `PostToolUse` hook fires on every
#   tool call in every session on this box — a process spawn and a write per call
#   is more than a liveness stamp is worth. `--min-interval` (default 300s) skips
#   a refresh while the beat is younger than the interval, and says so when it
#   does (`--verbose`). A skip is a decision, not a silence.
#
# EXIT CONTRACT — deliberately NOT the repo's 0/1/2 tri-state
#   Claude's hook contract gives exit 2 a meaning of its own: it BLOCKS the
#   session. A liveness stamp must never be able to block the work it reports on,
#   so this script exits 0 (beat written, or legitimately skipped) or 1 (could not
#   beat — the reason is named on stderr, which Claude shows without blocking).
#   The tri-state lives in the MESSAGE (`REFUSED —` / `CANNOT-ASSESS —`), and the
#   gate's own arm asserts the script never exits 2.
#
# Usage:
#   bash fleet/hooks/claude-beat.sh                       beat claude-session
#   bash fleet/hooks/claude-beat.sh --runtime claude-subagent
#   bash fleet/hooks/claude-beat.sh --print-install       the one-line install
#   bash fleet/hooks/claude-beat.sh --root DIR --cwd DIR --force --verbose
#
# ---knowledge---
# module_id: fleet.hooks.claude_beat
# system: fleet
# app: fleet
# solution_class: pattern
# patterns: []
# derives_from: null
# owner_sme: unassigned
# tier: L1
# interfaces: []
# invariants: ""
# gotchas: ""
# related: []
# do_not_duplicate: null
# ---knowledge---
#
set -u

self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
self="$self_dir/$(basename "${BASH_SOURCE[0]}")"
# The fleet tree that owns this hook: `<root>/fleet/hooks/claude-beat.sh`.
root="$(cd "$self_dir/../.." && pwd -P)"

runtime="claude-session"
state="running"
min_interval="300"
# The session's own tree: Claude sets CLAUDE_PROJECT_DIR for hooks, and an
# operator running this by hand means the directory they are standing in.
cwd="${CLAUDE_PROJECT_DIR:-$PWD}"
force=""
verbose=""
print_install=""

usage() {
  cat <<'USAGE'
Usage: bash fleet/hooks/claude-beat.sh [options]

  --runtime ID       the registered runtime id (default claude-session;
                     use claude-subagent from a SubagentStop hook)
  --state STATE      one of running|idle|paused|stopped (default running)
  --root DIR         the fleet tree the beat is written into (default: this
                     script's own tree — the one whose .fleet the judge reads)
  --cwd DIR          where the RUNNING commit is measured (default
                     $CLAUDE_PROJECT_DIR, else $PWD)
  --min-interval N   skip a refresh while the beat is younger than N seconds
                     (default 300; 0 always writes)
  --force            ignore the existing beat's age
  --verbose          print the skip/refusal detail (a refusal always prints)
  --print-install    print the one-line `hooks` value for ~/.claude/settings.json
  -h, --help         this text

Exit: 0 beat written or skipped / 1 could not beat (named on stderr).
Never 2: in Claude's hook contract exit 2 blocks the session.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime) runtime="${2:-}"; shift 2 ;;
    --state) state="${2:-}"; shift 2 ;;
    --root) root="${2:-}"; shift 2 ;;
    --cwd) cwd="${2:-}"; shift 2 ;;
    --min-interval) min_interval="${2:-}"; shift 2 ;;
    --force) force=1; shift ;;
    --verbose) verbose=1; shift ;;
    --print-install) print_install=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "claude-beat: unknown argument $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [[ -n "$print_install" ]]; then
  # ONE line, so the install is a paste rather than a hand-edit. The three events
  # are the three moments the two Claude runtimes have anything to report:
  # SessionStart (the session exists), PostToolUse (it is still working — this is
  # the refresh, rate-limited by --min-interval) and SubagentStop (a subagent
  # finished, which is the claude-subagent rung's report).
  printf '{"SessionStart":[{"hooks":[{"type":"command","command":"%s --min-interval %s"}]}],"PostToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"%s --min-interval %s"}]}],"SubagentStop":[{"hooks":[{"type":"command","command":"%s --runtime claude-subagent --min-interval %s"}]}]}\n' \
    "$self" "$min_interval" "$self" "$min_interval" "$self" "$min_interval"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "claude-beat: CANNOT-ASSESS — python3 not found, so no beat can be written" >&2
  exit 1
fi

args=( -m fleet.beats post --runtime "$runtime" --state "$state" --root "$root" --cwd "$cwd" --min-interval "$min_interval" )
if [[ -n "$force" ]]; then
  args+=( --force )
fi

out="$(env PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" python3 "${args[@]}" 2>&1)"
rc=$?
if [[ "$rc" -ne 0 ]]; then
  printf 'claude-beat: %s\n' "$out" >&2
  exit 1
fi
if [[ -n "$verbose" ]]; then
  printf 'claude-beat: %s\n' "$out"
fi
exit 0
