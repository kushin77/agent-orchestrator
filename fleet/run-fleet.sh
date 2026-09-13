#!/usr/bin/env bash
# run-fleet.sh — open BOTH sessions from a single command: the brain terminal
# and the dumb-terminal (sister) loop, side by side.
#
# Preference order: tmux (one window, two panes) -> konsole (two windows) ->
# background + foreground (no terminal emulator).
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

if command -v tmux >/dev/null 2>&1; then
  tmux kill-session -t fleet 2>/dev/null || true
  tmux new-session -d -s fleet -n sister "bash fleet/terminal.sh"
  tmux split-window -h -t fleet "bash fleet/brain.sh"
  tmux select-layout -t fleet even-horizontal
  exec tmux attach -t fleet
fi

if command -v konsole >/dev/null 2>&1; then
  konsole --new-tab -e bash fleet/terminal.sh 2>/dev/null &
  exec konsole -e bash fleet/brain.sh
fi

if command -v gnome-terminal >/dev/null 2>&1; then
  gnome-terminal -- bash fleet/terminal.sh &
  exec gnome-terminal -- bash fleet/brain.sh
fi

echo "No tmux/konsole/gnome-terminal found — running both in this terminal:"
echo "  sister loop -> background (log: /tmp/fleet-terminal.log)"
echo "  brain       -> foreground (idle-watch; Ctrl-C stops)"
bash fleet/terminal.sh run >/tmp/fleet-terminal.log 2>&1 &
echo "sister pid $!"
exec bash fleet/brain.sh
