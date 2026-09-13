#!/usr/bin/env bash
# brain.sh — the BRAIN session: advisor context, then idle-watch the whole
# channel as a live log. It blocks (never sleeps — idles) until a message or an
# escalation from the sister pings it.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

cat <<'BANNER'
==================================================================
  BRAIN — session fleet operating model (M26)
  You steer. The sister executes. Subagents build. Reports flow
  back here, and sister-side problems escalate to this terminal.
==================================================================
BANNER

echo "== standing directive =="
python3 fleet/channel.py verify --message fleet/directive.json 2>&1 || true

echo ""
echo "== channel =="
python3 fleet/channel.py status || true

echo ""
echo "== board =="
python3 governance/dispatch/cli.py status 2>/dev/null || true

echo ""
echo "== idle-watching the channel (Ctrl-C to stop) =="
exec python3 fleet/channel.py listen --timeout-seconds 0
