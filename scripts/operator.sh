#!/usr/bin/env bash
# operator.sh — the operator's way in, in one command (`make operator`, issue #763).
#
# WHY this is a script and not a three-line Makefile recipe: the rule that makes
# the target honest needs a real precondition with a real exit code. `make
# operator` reports every operator surface and then opens the live view, and it
# must NEVER print a success it cannot evidence — so when the box cannot host the
# view (no tmux) this exits non-zero, names the reason, and points at the same
# content without tmux.
#
# The layout is NOT reimplemented here. The live view is fleet/run-fleet.sh,
# which is `fleet/control.py live`, whose `live_layout()` is the single
# definition of the tmux session; `--dry-run` prints exactly those commands and
# builds nothing. A second copy of the layout would drift, and the drift is
# expensive (see fleet/README.md).
#
# Usage: bash scripts/operator.sh [--dry-run]
#
# ---knowledge---
# module_id: scripts.operator
# system: scripts
# app: scripts
# solution_class: pattern
# patterns: [dry-run-default]
# derives_from: null
# owner_sme: platform-sme
# tier: L0
# interfaces: []
# invariants: ""
# gotchas: ""
# related: ["#763"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail


# Bash-builtin dirname (no external `dirname`): the honesty check below strips
# PATH down to nothing to prove this path fails on a missing `tmux` by name
# rather than dying on a missing coreutil first.
_self="${BASH_SOURCE[0]}"
_self_dir="${_self%/*}"
[ "$_self_dir" = "$_self" ] && _self_dir="."
source "$_self_dir/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

printf 'operator surfaces — the way in (docs/OPERATOR-ACCESS.md):\n'
printf '  1. PRIMARY control plane — order the brain, then read its replies:\n'
printf '       python3 fleet/channel.py order --message <file-or-inline-json>\n'
printf '       python3 fleet/channel.py brain-inbox     # what the brain has to do\n'
printf '       python3 fleet/channel.py brain-outbox    # the brain answers (acks, refusals)\n'
printf '  2. override terminal — python3 fleet/control.py <verb>  (18 verbs;\n'
printf '     observe: status/health/debug/watch | steer: poke/pause/resume/override/\n'
printf '     refresh/update/halt | lifecycle: start/stop/kill/restart/cron)\n'
printf '  3. live view — this command, i.e. the tmux session (needs tmux)\n'
printf '  4. browser console — make console  (127.0.0.1:8787; fails closed without\n'
printf '     PORTAL_AUTH_GATE_JWKS_FILE, so it is loopback-or-nothing by default)\n'

case "${1:-}" in
  "")
    ;;
  --dry-run)
    printf '\n--dry-run: the tmux commands this would build, and nothing else:\n'
    exec python3 fleet/control.py live --dry-run
    ;;
  *)
    printf 'operator: unknown argument %s\n' "$1" >&2
    printf 'usage: bash scripts/operator.sh [--dry-run]\n' >&2
    exit 2
    ;;
esac

if ! command -v tmux >/dev/null 2>&1; then
  printf '\noperator: NOT-OK — tmux is not installed, so the live view cannot be hosted on this box.\n' >&2
  printf '  The fleet itself is unaffected; the same content without tmux:\n' >&2
  printf '    python3 fleet/console.py           # self-refreshing dashboard (Ctrl-C exits)\n' >&2
  printf '    python3 fleet/console.py --once    # one frame, for a script or a log\n' >&2
  printf '    python3 fleet/control.py watch     # follow the audit stream live\n' >&2
  printf '    make console                       # the browser console (loopback)\n' >&2
  exit 1
fi

printf '\nstarting the live view (rungs that are missing, then attach) — detach with Ctrl-b d\n'
exec bash fleet/run-fleet.sh
