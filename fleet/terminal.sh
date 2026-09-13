#!/usr/bin/env bash
# terminal.sh — the DUMB-TERMINAL (sister) session: a never-idle loop that
# watches the inbox, runs one subagent per directive via the agent CLI, reports
# the result and escalates failures. It never exits on an empty inbox.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

cat <<'BANNER'
==================================================================
  SISTER — dumb terminal (DeepSeek v4.1 Flash, no thinking)
  Watches .fleet/inbox forever, executes only brain directives,
  reports results, escalates problems. Never idles out.
==================================================================
BANNER

exec python3 fleet/terminal.py run "$@"
