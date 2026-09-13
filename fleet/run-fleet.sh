#!/usr/bin/env bash
# run-fleet.sh — start whichever rungs are missing, guard-aware, and attach if
# tmux is present. It never kills and recreates a fleet that is already up:
# the singleton rung locks make a blind recreate fail — measured, `tmux
# kill-session + new-session` over a running fleet starts panes whose loops are
# refused by the guard and die, and the operator sees `[exited]`.
#
# Canonical start is `python3 fleet/control.py start`; this is the one-command
# convenience wrapper over the same rule: only missing rungs are started, stale
# queue flags are cleared first.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

up() { pgrep -f "$1" >/dev/null 2>&1; }

sister_up=; up 'fleet/terminal.py' && sister_up=1
brain_up=; up 'fleet/brain.py' && brain_up=1

if [ -n "$sister_up" ] && [ -n "$brain_up" ]; then
  echo "fleet: both rungs already running"
  python3 fleet/channel.py status || true
  if command -v tmux >/dev/null 2>&1 && tmux has-session -t fleet 2>/dev/null; then
    exec tmux attach -t fleet
  fi
  exit 0
fi

if [ -z "$sister_up" ]; then
  rm -f .fleet/paused .fleet/stopping
  echo "fleet: cleared stale pause/stop flags"
fi

if command -v tmux >/dev/null 2>&1 && [ -z "$sister_up" ] && [ -z "$brain_up" ]; then
  tmux kill-session -t fleet 2>/dev/null || true
  tmux new-session -d -s fleet -n sister "bash fleet/terminal.sh"
  tmux split-window -h -t fleet "bash fleet/brain.sh"
  tmux select-layout -t fleet even-horizontal
  tmux attach -t fleet 2>/dev/null || {
    echo "fleet: started in tmux (attach with: tmux attach -t fleet)"
    exit 0
  }
  exit 0
fi

# No tmux, or only one rung missing: start the missing ones detached.
[ -n "$sister_up" ] || { setsid bash fleet/terminal.sh >/tmp/fleet-terminal.log 2>&1 & echo "fleet: started sister (log: /tmp/fleet-terminal.log)"; }
[ -n "$brain_up" ] || { setsid bash fleet/brain.sh >/tmp/fleet-brain.log 2>&1 & echo "fleet: started brain (log: /tmp/fleet-brain.log)"; }

sleep 3
python3 fleet/channel.py status || true
