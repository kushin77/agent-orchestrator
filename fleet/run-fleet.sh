#!/usr/bin/env bash
# run-fleet.sh — the one-command way in: start whatever is missing, then attach to
# the live fleet session.
#
# WHY it is a thin wrapper: the tmux layout has exactly one definition,
# `control.live_layout()`, because two copies of it drift and the failure is
# expensive. Two rules it must never break:
#
#   * never kill a healthy fleet — a blind `tmux kill-session` + `new-session` over
#     a running fleet starts panes whose loops the singleton guard refuses, and the
#     operator just sees `[exited]` (measured). `live` reuses an existing session.
#   * the session is a VIEW, not the host — the rungs run detached and the
#     watchdog/cron owns their lifecycle, so attaching or detaching never touches a
#     run in flight. Each window tails `.fleet/<rung>.log`.
#
# Usage: bash fleet/run-fleet.sh        (equivalent: python3 fleet/control.py live)
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

exec python3 fleet/control.py live
