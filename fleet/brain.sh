#!/usr/bin/env bash
# brain.sh — the BRAIN rung. Operator orders arrive here; this process turns
# each one into a brain-signed directive for the sister and reports back.
#
# It is a loop, not a prompt: the operator's trigger is `channel.py order`, and
# nothing here needs a human to type into it. Use `fleet/control.py` for the
# human override (refresh/update/poke/halt/debug/watch).
#
# ---knowledge---
# module_id: fleet.brain
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
  BRAIN — session fleet operating model (M26)
  Hierarchy: operator -> BRAIN -> sister -> subagents
  Order:   python3 fleet/channel.py order --message '<json>'
  Reports: python3 fleet/channel.py brain-outbox
==================================================================
BANNER

echo "== contract =="
python3 fleet/channel.py verify --message fleet/directive.json 2>&1 || true

echo ""
echo "== channel =="
python3 fleet/channel.py status || true

echo ""
echo "== board =="
python3 governance/dispatch/cli.py status 2>/dev/null || true

echo ""
echo "== brain loop (dispatching the operator's orders; Ctrl-C to stop) =="
exec python3 fleet/brain.py run
