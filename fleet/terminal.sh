#!/usr/bin/env bash
# terminal.sh — the DUMB-TERMINAL (sister) session: a never-idle loop that
# watches the inbox, runs one subagent per directive via the agent CLI, reports
# the result and escalates failures. It never exits on an empty inbox.
#
# ---knowledge---
# module_id: fleet.terminal
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

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

cat <<'BANNER'
==================================================================
  SISTER — dumb terminal (DeepSeek v4.1 Flash, no thinking)
  Watches .fleet/inbox forever, executes only BRAIN directives,
  reports results, escalates up to the brain. Never idles out.
  Orders come from the brain: python3 fleet/channel.py order ...
==================================================================
BANNER

exec python3 fleet/terminal.py run "$@"
